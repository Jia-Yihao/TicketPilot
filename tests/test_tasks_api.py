from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.conftest import ALICE, CAROL


def _create(client: TestClient, headers: dict[str, str], request: str, **context: object) -> dict:
    response = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"request": request, "context": context},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_order_query_returns_owned_order(client: TestClient) -> None:
    body = _create(client, ALICE, "帮我查询订单 ORD-DEMO-1001")

    assert body["status"] == "SUCCEEDED"
    assert body["result"]["outcome"] == "VERIFIED_SUCCESS"
    assert body["result"]["data"]["order_id"] == "ORD-DEMO-1001"
    assert body["result"]["evidence"] == [{"type": "order", "reference": "ORD-DEMO-1001"}]
    assert body["tool_execution"] is None


def test_missing_order_id_asks_for_clarification(client: TestClient) -> None:
    body = _create(client, ALICE, "帮我查一下订单")

    assert body["status"] == "SUCCEEDED"
    assert body["result"]["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert "订单号" in body["result"]["summary"]


def test_logistics_query_returns_tracking(client: TestClient) -> None:
    body = _create(client, ALICE, "订单 ORD-DEMO-1002 的物流到哪了？")

    assert body["status"] == "SUCCEEDED"
    assert body["result"]["outcome"] == "VERIFIED_SUCCESS"
    assert body["result"]["data"]["tracking_no"] == "DE10020002"
    assert body["result"]["data"]["available"] is True


def test_policy_query_uses_active_version_only(client: TestClient) -> None:
    body = _create(client, ALICE, "退款条件是什么？")

    assert body["status"] == "SUCCEEDED"
    assert body["result"]["outcome"] == "VERIFIED_SUCCESS"
    citations = body["result"]["evidence"]
    assert citations, "policy citations must be present"
    assert all(item["version"] != "v2" for item in citations)
    assert citations[0]["version"] == "v3"
    assert "15 天" not in body["result"]["summary"]
    assert "本文件仅用于" not in body["result"]["summary"]
    assert "30 天" in body["result"]["summary"]


def test_unknown_request_is_clarified_not_guessed(client: TestClient) -> None:
    body = _create(client, ALICE, "今天天气怎么样？")

    assert body["status"] == "SUCCEEDED"
    assert body["result"]["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert body["result"]["checks"] == [
        {"name": "required_slots_present", "passed": False}
    ]


def test_cross_tenant_order_is_not_disclosed(client: TestClient) -> None:
    body = _create(client, CAROL, "查询订单 ORD-DEMO-1001")

    assert body["status"] == "FAILED"
    assert body["result"]["summary"] == "Order not found."
    assert "alice" not in json.dumps(body, ensure_ascii=False)
    assert "12900" not in json.dumps(body, ensure_ascii=False)


def test_task_fetch_and_event_replay(client: TestClient) -> None:
    created = _create(client, ALICE, "帮我查询订单 ORD-DEMO-1001")

    fetched = client.get(f"/api/v1/tasks/{created['task_id']}", headers=ALICE)
    assert fetched.status_code == 200
    assert fetched.json()["task_id"] == created["task_id"]

    events = client.get(f"/api/v1/tasks/{created['task_id']}/events", headers=ALICE)
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: task.created" in events.text
    assert '"created_at"' in events.text
    assert "event: tool.call_completed" in events.text
    assert "event: task.succeeded" in events.text

    first_id = next(
        line.removeprefix("id: ")
        for line in events.text.splitlines()
        if line.startswith("id: ")
    )
    tail = client.get(
        f"/api/v1/tasks/{created['task_id']}/events",
        headers={**ALICE, "Last-Event-ID": first_id},
    )
    assert "event: task.created" not in tail.text
    assert "event: task.succeeded" in tail.text


def test_identity_headers_are_required(client: TestClient) -> None:
    response = client.get(
        "/api/v1/tasks/task_missing", headers={"X-Request-ID": "req-test-1"}
    )

    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "UNAUTHORIZED"
    assert error["request_id"] == "req-test-1"
    assert response.headers["X-Request-ID"] == "req-test-1"


def test_validation_error_reports_fields_not_values(client: TestClient) -> None:
    """A body that cannot be parsed is a 422, and its content stays out."""
    raw = client.post(
        "/api/v1/support/messages", headers=ALICE, content=b"password=Tr3m0d1a"
    )
    assert raw.status_code == 422, raw.text
    assert "Tr3m0d1a" not in raw.text

    wrong_type = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": ["MARKER_LEAK_TEST"]},
    )
    assert wrong_type.status_code == 422, wrong_type.text
    error = wrong_type.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "MARKER_LEAK_TEST" not in wrong_type.text
    assert error["details"]["errors"][0]["loc"] == ["body", "message"]


def test_health_endpoints(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["database"] == "ok"
