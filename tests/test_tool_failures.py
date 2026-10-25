"""No failure form may pass in silence.

A tool can fail in six ways, and each one has to leave two marks: something
the customer can read, and something the operator can find later. This file
walks the six forms and checks both marks every time.

The forms are the ones the manifest promises to answer: a bad argument, a
timeout, a broken upstream, an outcome nobody knows, a business rejection,
and a question the SQL planner refuses.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import (
    ConflictError,
    ToolTimeoutError,
    UnknownOutcomeError,
    UpstreamUnavailableError,
)
from app.core.security import Identity
from app.models.audit import AuditEvent
from app.models.sql_query import SqlQuery
from app.tools.base import (
    DegradationStep,
    RetryPolicy,
    ToolContext,
    ToolManifest,
    ToolResult,
)
from app.tools.executor import ToolExecutionError
from app.tools.gateway import ToolGateway
from app.tools.registry import ToolRegistry


def _ctx() -> ToolContext:
    return ToolContext(
        identity=Identity("demo", "alice", frozenset()),
        task_id="task-1",
        idempotency_key="task-1:charge_refund",
    )


def _manifest(**overrides: object) -> ToolManifest:
    values: dict[str, object] = {
        "name": "charge_refund",
        "description": "a tool that moves money",
        "params": {"type": "object", "properties": {}, "additionalProperties": False},
        "side_effect": True,
        "idempotency": "required",
        "timeout_ms": 3000,
        "retry": RetryPolicy(max_attempts=2, backoff_ms=10, retry_on=("timeout", "upstream")),
        "degradation": (
            DegradationStep(kind="ask_user", message="请再确认一次。", action="clarify"),
            DegradationStep(kind="retry_later", message="稍后自动重试。", action="retry"),
            DegradationStep(kind="stop", message="处理中，稍后通知你。", action="pending"),
            DegradationStep(kind="handoff", message="已转人工。", action="handoff"),
        ),
    }
    values.update(overrides)
    return ToolManifest(**values)  # type: ignore[arg-type]


def _failing_gateway(session: Session, exc: BaseException) -> ToolGateway:
    registry = ToolRegistry()
    manifest = _manifest()

    def handler(ctx: ToolContext, args: dict) -> ToolResult:
        raise exc

    registry.register(manifest, handler)
    return ToolGateway(session, registry)


def _audit_rows(app: FastAPI, action: str) -> list[AuditEvent]:
    with app.state.session_factory() as session:
        return list(
            session.scalars(select(AuditEvent).where(AuditEvent.action == action))
        )


# ------------------------------------------------------------- 1. a bad argument
def test_a_bad_argument_is_asked_about_and_recorded(client: TestClient) -> None:
    """Form 1: the argument is wrong, so the customer is asked instead."""
    created = client.post(
        "/api/v1/tasks",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"request": "帮我查一下订单状态", "context": {"order_id": "BAD-ID"}},
    )
    task = created.json()

    assert task["status"] == "SUCCEEDED"
    assert task["result"]["summary"]
    assert "ORD-" in task["result"]["summary"]

    trace = client.get(
        f"/api/v1/traces/{task['trace_id']}",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    ).json()
    degraded = [span for span in trace["spans"] if span["kind"] == "guard"]
    assert degraded
    assert degraded[0]["attributes"]["degradation"] == "ask_user"


# ------------------------------------------------------------------ 2. a timeout
def test_a_timeout_is_reported_and_written_down(app: FastAPI) -> None:
    """Form 2: the upstream did not answer in time."""
    gateway = _failing_gateway(app, ToolTimeoutError("上游超时。"))

    with app.state.session_factory() as session:
        gateway = ToolGateway(session, gateway.registry)
        with pytest.raises(ToolExecutionError) as caught:
            gateway.call_outcome("charge_refund", _ctx(), {})
        session.commit()

    assert caught.value.kind.value == "timeout"
    assert caught.value.attempts == 2
    assert caught.value.degradation is not None
    assert caught.value.degradation.kind == "retry_later"

    rows = _audit_rows(app, "tool.charge_refund")
    assert len(rows) == 1
    assert rows[0].outcome == "ERROR"
    assert rows[0].detail["failure_kind"] == "timeout"
    assert rows[0].detail["attempts"] == 2
    assert rows[0].detail["degradation"] == "retry_later"


# --------------------------------------------------------------- 3. a 5xx upstream
def test_a_broken_upstream_is_reported_and_written_down(app: FastAPI) -> None:
    """Form 3: the upstream answered with a failure."""
    with app.state.session_factory() as session:
        gateway = _failing_gateway(session, UpstreamUnavailableError("上游 503。"))
        with pytest.raises(ToolExecutionError) as caught:
            gateway.call_outcome("charge_refund", _ctx(), {})
        session.commit()

    assert caught.value.kind.value == "upstream"
    assert caught.value.degradation is not None
    assert caught.value.degradation.kind == "retry_later"

    rows = _audit_rows(app, "tool.charge_refund")
    assert len(rows) == 1
    assert rows[0].detail["failure_kind"] == "upstream"


# ----------------------------------------------------------- 4. an unknown outcome
def test_an_unknown_outcome_is_not_repeated_and_is_written_down(app: FastAPI) -> None:
    """Form 4: the call may already have taken effect, so it is not repeated."""
    with app.state.session_factory() as session:
        gateway = _failing_gateway(session, UnknownOutcomeError("结果未知。"))
        with pytest.raises(ToolExecutionError) as caught:
            gateway.call_outcome("charge_refund", _ctx(), {})
        session.commit()

    assert caught.value.kind.value == "unknown"
    assert caught.value.attempts == 1
    assert caught.value.degradation is not None
    assert caught.value.degradation.kind == "stop"

    rows = _audit_rows(app, "tool.charge_refund")
    assert len(rows) == 1
    assert rows[0].detail["failure_kind"] == "unknown"
    assert rows[0].detail["attempts"] == 1


# --------------------------------------------------------- 5. a business rejection
def test_a_business_rejection_is_reported_and_written_down(app: FastAPI) -> None:
    """Form 5: the tool answered, and the answer is no."""
    with app.state.session_factory() as session:
        gateway = _failing_gateway(session, ConflictError("该订单已有退款在处理。"))
        with pytest.raises(ToolExecutionError) as caught:
            gateway.call_outcome("charge_refund", _ctx(), {})
        session.commit()

    assert caught.value.kind.value == "business"
    assert caught.value.attempts == 1
    assert caught.value.degradation is not None
    assert caught.value.degradation.kind == "stop"

    rows = _audit_rows(app, "tool.charge_refund")
    assert len(rows) == 1
    assert rows[0].detail["failure_kind"] == "business"


# --------------------------------------------------------------- 6. a SQL refusal
def test_a_refused_question_is_answered_by_a_human_and_kept_in_the_ledger(
    client: TestClient, app: FastAPI
) -> None:
    """Form 6: the question was understood, but the statement may not run."""
    response = client.post(
        "/api/v1/support/messages",
        json={"message": "我的订单有多少笔已到账"},
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    payload = response.json()

    assert payload["reply"]
    assert "转人工" in payload["reply"]
    assert payload["handoff"] is not None

    with app.state.session_factory() as session:
        refused = list(session.scalars(select(SqlQuery)))
    assert refused
    assert refused[0].status == "REJECTED"
    assert refused[0].rejected_reason
