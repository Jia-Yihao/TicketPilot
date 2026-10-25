from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, CAROL


def _create_ticket(
    client: TestClient,
    key: str,
    headers: dict[str, str] = ALICE,
    subject: str = "物流延迟",
) -> dict:
    response = client.post(
        "/api/v1/tickets",
        headers={**headers, "Idempotency-Key": key},
        json={
            "category": "LOGISTICS",
            "priority": "HIGH",
            "subject": subject,
            "description": "包裹停留超过 48 小时，请人工跟进。",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_ticket_creation_is_idempotent(client: TestClient) -> None:
    first = _create_ticket(client, "ticket-key-1")
    second = _create_ticket(client, "ticket-key-1")

    assert first["ticket_id"] == second["ticket_id"]
    assert first["ticket_no"].startswith("TCK-")
    assert first["status"] == "OPEN"


def test_idempotency_key_is_scoped_by_tenant(client: TestClient) -> None:
    first = _create_ticket(client, "shared-key", ALICE)
    second = _create_ticket(client, "shared-key", CAROL)

    assert first["ticket_id"] != second["ticket_id"]


def test_ticket_visibility_is_owner_scoped(client: TestClient) -> None:
    created = _create_ticket(client, "visibility-key")

    own = client.get(f"/api/v1/tickets/{created['ticket_id']}", headers=ALICE)
    assert own.status_code == 200

    other_user = client.get(f"/api/v1/tickets/{created['ticket_id']}", headers=BOB)
    assert other_user.status_code == 404

    other_tenant = client.get(f"/api/v1/tickets/{created['ticket_id']}", headers=CAROL)
    assert other_tenant.status_code == 404


def test_agent_creates_ticket_through_bounded_tool(client: TestClient) -> None:
    response = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={"request": "帮我创建一个工单，投诉物流一直不更新"},
    )
    assert response.status_code == 201
    body = response.json()

    assert body["status"] == "SUCCEEDED"
    assert body["result"]["outcome"] == "VERIFIED_SUCCESS"
    assert body["result"]["data"]["ticket_no"].startswith("TCK-")
    assert body["result"]["data"]["created"] is True


def test_agent_rejects_unknown_ticket_category(client: TestClient) -> None:
    response = client.post(
        "/api/v1/tickets",
        headers={**ALICE, "Idempotency-Key": "bad-category"},
        json={
            "category": "SOMETHING",
            "priority": "NORMAL",
            "subject": "测试",
            "description": "测试",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_policy_tenant_isolation(client: TestClient) -> None:
    response = client.post(
        "/api/v1/tasks",
        headers=CAROL,
        json={"request": "退款条件是什么？"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "SUCCEEDED"
    assert body["result"]["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert body["result"]["evidence"] == []
