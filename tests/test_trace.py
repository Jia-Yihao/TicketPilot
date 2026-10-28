from __future__ import annotations

from fastapi.testclient import TestClient

from app.observability.redaction import redact, redact_payload
from app.observability.tracing import TraceRecorder


def test_recorder_records_spans_in_order() -> None:
    recorder = TraceRecorder(tenant_id="demo")
    recorder.add("understand", "support.classify", attributes={"intent": "POLICY_QA"})
    recorder.add("retrieve", "policy.search", attributes={"hits": 2})

    assert recorder.kinds() == ["understand", "retrieve"]
    assert recorder.attributes_of("retrieve") == {"hits": 2}
    assert recorder.find("missing") is None


def test_recorder_marks_a_failed_span_as_error() -> None:
    recorder = TraceRecorder(tenant_id="demo")
    try:
        with recorder.span("tool", "get_order") as payload:
            payload["tool"] = "get_order"
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    span = recorder.find("tool")
    assert span is not None
    assert span.status == "ERROR"
    assert span.attributes["error"] is True


def test_nested_spans_point_at_their_parent() -> None:
    recorder = TraceRecorder(tenant_id="demo")
    with recorder.span("understand", "outer"):
        with recorder.span("retrieve", "inner"):
            pass

    inner = recorder.find("retrieve")
    outer = recorder.find("understand")
    assert inner is not None and outer is not None
    assert inner.parent_span_id == outer.id
    assert outer.parent_span_id is None


def test_redaction_masks_personal_data() -> None:
    masked = redact("我的手机号 13800138000，邮箱 alice@example.com")
    assert "13800138000" not in masked
    assert "alice@example.com" not in masked
    assert "***" in masked


def test_redaction_keeps_business_identifiers_readable() -> None:
    masked = redact("订单 ORD-DEMO-1001 已签收")
    assert "ORD-DEMO-1001" in masked


def test_redaction_walks_nested_payloads() -> None:
    payload = redact_payload({"contact": {"phone": "13900001111"}, "items": ["13500002222"]})
    assert payload["contact"]["phone"] == "***"
    assert payload["items"] == ["***"]


def test_trace_attributes_are_redacted_before_storage() -> None:
    recorder = TraceRecorder(tenant_id="demo")
    recorder.add("generate", "support.reply", attributes={"text": "联系 13800138000"})

    assert "13800138000" not in recorder.spans[0].attributes["text"]


def test_support_reply_returns_a_replayable_trace(client: TestClient) -> None:
    response = client.post(
        "/api/v1/support/messages",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"message": "退款政策规定哪些订单可以退款"},
    )
    assert response.status_code == 200
    trace_id = response.json()["trace_id"]
    assert trace_id

    trace = client.get(
        f"/api/v1/traces/{trace_id}",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert trace.status_code == 200
    body = trace.json()
    kinds = [span["kind"] for span in body["spans"]]
    assert kinds == ["understand", "retrieve", "generate"]

    understand = body["spans"][0]["attributes"]
    assert understand["intent"] == "POLICY_QA"
    retrieve = body["spans"][1]["attributes"]
    assert retrieve["hits"] >= 1
    assert retrieve["citations"][0]["version"] == "v3"


def test_trace_of_a_refund_task_records_the_tool_and_the_approval(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/v1/tasks",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题"},
    )
    assert created.status_code == 201
    task = created.json()
    assert task["status"] == "WAITING_APPROVAL"
    assert task["trace_id"]

    trace = client.get(
        f"/api/v1/traces/{task['trace_id']}",
        headers={
            "X-Tenant-Id": "demo",
            "X-User-Id": "reviewer",
            "X-Roles": "approver",
        },
    )
    assert trace.status_code == 200
    kinds = [span["kind"] for span in trace.json()["spans"]]
    assert "understand" in kinds
    assert "tool" in kinds
    assert "approval" in kinds


def test_trace_of_another_tenant_is_not_found(client: TestClient) -> None:
    response = client.post(
        "/api/v1/support/messages",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"message": "退款政策规定哪些订单可以退款"},
    )
    trace_id = response.json()["trace_id"]

    other_tenant = client.get(
        f"/api/v1/traces/{trace_id}",
        headers={"X-Tenant-Id": "acme", "X-User-Id": "carol"},
    )
    assert other_tenant.status_code == 404


def test_trace_of_another_user_is_not_found(client: TestClient) -> None:
    response = client.post(
        "/api/v1/support/messages",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"message": "退款政策规定哪些订单可以退款"},
    )
    trace_id = response.json()["trace_id"]

    other_user = client.get(
        f"/api/v1/traces/{trace_id}",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "bob"},
    )
    assert other_user.status_code == 404


def test_approving_a_refund_appends_to_the_task_trace(client: TestClient) -> None:
    created = client.post(
        "/api/v1/tasks",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={
            "request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元",
            "context": {"refund_reason": "QUALITY"},
        },
    )
    assert created.status_code == 201
    task = created.json()
    trace_id = task["trace_id"]
    approval_id = task["pending_approval"]["approval_id"]

    approved = client.post(
        f"/api/v1/tasks/{task['task_id']}/approve",
        headers={
            "X-Tenant-Id": "demo",
            "X-User-Id": "reviewer",
            "X-Roles": "approver",
        },
        json={"approval_id": approval_id, "decision": "APPROVE"},
    )
    assert approved.status_code == 200

    trace = client.get(
        f"/api/v1/traces/{trace_id}",
        headers={
            "X-Tenant-Id": "demo",
            "X-User-Id": "reviewer",
            "X-Roles": "approver",
        },
    )
    assert trace.status_code == 200
    names = [span["name"] for span in trace.json()["spans"]]
    assert "refund.approval_requested" in names
    assert "refund.approval_approved" in names
    assert "refund.execution" in names


def test_trace_metrics_are_tenant_scoped_and_staff_only(client: TestClient) -> None:
    client.post(
        "/api/v1/support/messages",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"message": "退款政策规定哪些订单可以退款"},
    )

    response = client.get(
        "/api/v1/traces/metrics",
        headers={
            "X-Tenant-Id": "demo",
            "X-User-Id": "reviewer",
            "X-Roles": "approver",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["spans"] > 0
    assert body["trace_count"] >= 1
    assert body["error_spans"] == 0
    assert body["error_rate"] == 0.0
    assert body["by_kind"]

    forbidden = client.get(
        "/api/v1/traces/metrics",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert forbidden.status_code == 403

    other_tenant = client.get(
        "/api/v1/traces/metrics",
        headers={
            "X-Tenant-Id": "acme",
            "X-User-Id": "agent-9",
            "X-Roles": "support",
        },
    )
    assert other_tenant.status_code == 200
    assert other_tenant.json()["spans"] == 0
    assert other_tenant.json()["error_rate"] == 0.0
