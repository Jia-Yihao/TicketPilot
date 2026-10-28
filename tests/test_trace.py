from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.turns import Draft, SupportIntent
from app.core.security import Identity
from app.observability import tracing
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


# --------------------------------------------------- R9-a: segment latencies
#
# R9 口径 §5 acceptance 2: stub the single entry ``tracing.elapsed_ms`` to one
# fixed non-zero reading and assert every segment span carries it. A real
# elapsed-time reading is not admissible as the criterion — 0 is a legal
# reading and would be indistinguishable from a producer that forgot to pass
# latency_ms at all (§2.2.2). Readings below come from the stub, never from
# wall-clock assertions or manufactured delays.


def _stubbed_spans(
    client: TestClient, headers: dict[str, str], trace_id: str
) -> dict[tuple[str, str], int]:
    response = client.get(f"/api/v1/traces/{trace_id}", headers=headers)
    assert response.status_code == 200
    return {
        (span["kind"], span["name"]): span["latency_ms"]
        for span in response.json()["spans"]
    }


def test_stubbed_clock_reads_seven_on_every_conversation_segment(
    app_factory: Callable[..., FastAPI],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tracing, "elapsed_ms", lambda started: 7)
    application = app_factory()
    client = TestClient(application)
    alice = {"X-Tenant-Id": "demo", "X-User-Id": "alice"}

    profiled = client.put(
        "/api/v1/memory", headers=alice, json={"preferred_name": "Alice 女士"}
    )
    assert profiled.status_code == 200

    policy_turn = client.post(
        "/api/v1/support/messages",
        headers=alice,
        json={"message": "退款条件是什么？"},
    )
    assert policy_turn.status_code == 200
    policy_spans = _stubbed_spans(
        client, alice, str(policy_turn.json()["trace_id"])
    )
    assert policy_spans[("understand", "support.classify")] == 7
    assert policy_spans[("retrieve", "policy.search")] == 7
    assert policy_spans[("generate", "memory.personalize")] == 7
    assert policy_spans[("generate", "support.reply")] == 7

    analytics_turn = client.post(
        "/api/v1/support/messages",
        headers=alice,
        json={"message": "我买了多少订单"},
    )
    assert analytics_turn.status_code == 200
    analytics_spans = _stubbed_spans(
        client, alice, str(analytics_turn.json()["trace_id"])
    )
    assert analytics_spans[("generate", "analytics.narrate")] == 7
    assert analytics_spans[("guard", "analytics.faithfulness")] == 7


def test_stubbed_clock_reads_seven_on_the_task_route(
    app_factory: Callable[..., FastAPI],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tracing, "elapsed_ms", lambda started: 7)
    application = app_factory()
    client = TestClient(application)
    alice = {"X-Tenant-Id": "demo", "X-User-Id": "alice"}

    created = client.post(
        "/api/v1/tasks",
        headers=alice,
        json={
            "request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题"
        },
    )
    assert created.status_code == 201
    task = created.json()
    assert task["trace_id"]

    task_spans = _stubbed_spans(client, alice, str(task["trace_id"]))
    assert task_spans[("understand", "task.route")] == 7


def test_stubbed_clock_reads_seven_when_the_output_guard_changes_the_draft(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The guard span only exists when the guard really rewrote the reply
    # (§5.2): the reply below carries a timeline assertion and the draft has
    # no source, so ``_guard_output`` cannot take either early return.
    monkeypatch.setattr(tracing, "elapsed_ms", lambda started: 7)
    agent: Any = app.state.support_agent
    recorder = TraceRecorder(tenant_id="demo")
    draft = Draft(intent=SupportIntent.POLICY_QA, reply="您的退款 3 天内一定到账。")

    with app.state.session_factory() as session:
        agent._guard_output(
            session,
            Identity("demo", "alice", frozenset()),
            recorder,
            "conv-demo",
            draft,
        )

    span = recorder.find("guard")
    assert span is not None
    assert span.name == "output.grounded"
    assert span.latency_ms == 7


def test_analytics_tool_spans_carry_their_duration_in_the_latency_column(
    client: TestClient,
) -> None:
    # §2.1.3: the ledger/attribute duration_ms and the latency column are the
    # same number written into a second carrier — never a second formula.
    answered = client.post(
        "/api/v1/support/messages",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"message": "我买了多少订单"},
    )
    assert answered.status_code == 200
    response = client.get(
        f"/api/v1/traces/{answered.json()['trace_id']}",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert response.status_code == 200
    tool_spans = [
        span
        for span in response.json()["spans"]
        if span["kind"] == "tool" and "duration_ms" in span["attributes"]
    ]
    assert tool_spans, "expected a tool span carrying duration_ms"
    for span in tool_spans:
        assert span["latency_ms"] == span["attributes"]["duration_ms"]
