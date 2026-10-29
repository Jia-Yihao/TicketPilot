from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.online_signals import get_online_signals
from app.core.policy import PolicyEngine
from app.core.security import Identity
from app.core.time import utcnow
from app.models.conversation import Conversation, ConversationMessage
from app.models.enums import HandoffReason, ToolExecutionStatus
from app.models.handoff import HandoffEvent
from app.models.task import Task
from app.models.tool_execution import ToolExecution
from app.services import signal_readings as readings_module
from app.services.signal_readings import (
    NO_EVIDENCE_INTENTS,
    SIGNAL_IDS,
    collect_readings,
    handoff_reading,
    no_evidence_reading,
    tool_failure_reading,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def session_factory(app_factory: Callable[[], object]) -> Callable[[], Session]:
    app = app_factory()
    return app.state.session_factory  # type: ignore[attr-defined,no-any-return]


@pytest.fixture
def policy() -> PolicyEngine:
    return PolicyEngine.from_settings(Settings(environment="test"))


def _identity(tenant: str) -> Identity:
    return Identity(tenant, "u1", frozenset())


def _conv(session: Session, tenant: str, cid: str, *, at=None) -> Conversation:
    moment = at or utcnow()
    conv = Conversation(
        id=cid, tenant_id=tenant, user_id="u1", created_at=moment, updated_at=moment
    )
    session.add(conv)
    return conv


def _msg(
    session: Session, cid: str, *, role: str, intent: str | None, at=None
) -> ConversationMessage:
    message = ConversationMessage(
        id=f"cm_{uuid.uuid4().hex[:16]}",
        conversation_id=cid,
        role=role,
        intent=intent,
        content="x",
        created_at=at or utcnow(),
    )
    session.add(message)
    return message


def _handoff(
    session: Session, tenant: str, cid: str, reason: str, *, at=None
) -> HandoffEvent:
    event = HandoffEvent(
        id=f"hnd_{uuid.uuid4().hex[:16]}",
        tenant_id=tenant,
        user_id="u1",
        conversation_id=cid,
        signal_id="no_evidence" if reason == HandoffReason.NO_EVIDENCE.value else "other",
        reason=reason,
        priority="P2",
        created_at=at or utcnow(),
    )
    session.add(event)
    return event


def _task(session: Session, tenant: str, tid: str) -> Task:
    task = Task(id=tid, tenant_id=tenant, user_id="u1", request="r")
    session.add(task)
    return task


def _tool_exec(
    session: Session, tid: str, te_id: str, status: ToolExecutionStatus, *, at=None
) -> ToolExecution:
    ex = ToolExecution(
        id=te_id,
        task_id=tid,
        tool_name="t",
        request_digest="d",
        idempotency_key=te_id,
        status=status,
        created_at=at or utcnow(),
    )
    session.add(ex)
    return ex


def test_signal_a_full_history_numerator_and_denominator(session_factory) -> None:
    now = utcnow()
    with session_factory() as session:
        _conv(session, "iso-a", "c1")
        _msg(session, "c1", role="user", intent="POLICY_QA", at=now)
        _msg(session, "c1", role="user", intent="SELF_ANALYTICS", at=now)
        # excluded from denominator: off-intent user msg, and a POLICY_QA assistant msg
        _msg(session, "c1", role="user", intent="ORDER_STATUS", at=now)
        _msg(session, "c1", role="assistant", intent="POLICY_QA", at=now)
        _handoff(session, "iso-a", "c1", HandoffReason.NO_EVIDENCE.value, at=now)
        _handoff(session, "iso-a", "c1", HandoffReason.NO_EVIDENCE.value, at=now)
        _handoff(session, "iso-a", "c1", HandoffReason.FRUSTRATION.value, at=now)
        session.flush()
        reading = no_evidence_reading(session, _identity("iso-a"))
    assert reading.signal_id == "no_evidence_rate"
    assert reading.numerator == 2
    assert reading.denominator == 2
    assert reading.metric_value == 1.0


def test_signal_a_window_excludes_out_of_range(session_factory) -> None:
    now = utcnow()
    old = now - timedelta(minutes=120)
    with session_factory() as session:
        _conv(session, "iso-a", "c1")
        _msg(session, "c1", role="user", intent="POLICY_QA", at=now)
        _msg(session, "c1", role="user", intent="POLICY_QA", at=old)
        _handoff(session, "iso-a", "c1", HandoffReason.NO_EVIDENCE.value, at=now)
        _handoff(session, "iso-a", "c1", HandoffReason.NO_EVIDENCE.value, at=old)
        session.flush()
        reading = no_evidence_reading(session, _identity("iso-a"), window_minutes=60)
    assert reading.numerator == 1
    assert reading.denominator == 1
    assert reading.window_minutes == 60


def test_signal_a_numerator_only_from_reason(session_factory) -> None:
    # Behavior: numerator is driven only by HandoffEvent.reason, not by messages.
    now = utcnow()
    with session_factory() as session:
        _conv(session, "iso-a", "c1")
        # many qualifying messages but zero NO_EVIDENCE events -> numerator 0
        for _ in range(3):
            _msg(session, "c1", role="user", intent="POLICY_QA", at=now)
        _handoff(session, "iso-a", "c1", HandoffReason.FRUSTRATION.value, at=now)
        session.flush()
        reading = no_evidence_reading(session, _identity("iso-a"))
    assert reading.numerator == 0
    assert reading.denominator == 3
    assert reading.metric_value == 0.0


def test_signal_a_intent_copy_matches_live_escalation_yaml() -> None:
    raw = yaml.safe_load((REPO_ROOT / "app/agent/escalation.yaml").read_text(encoding="utf-8"))
    blocks = {sig["signal_id"]: sig for sig in raw["signals"]}
    yaml_intents = set(blocks["no_evidence"]["intents"])
    assert yaml_intents == {"POLICY_QA", "SELF_ANALYTICS"}
    assert set(NO_EVIDENCE_INTENTS) == yaml_intents


def test_signal_a_source_scan_never_reads_json_columns() -> None:
    src = inspect.getsource(readings_module)
    for forbidden in ("attributes", "detail", "bundle", "payload"):
        assert forbidden not in src, f"forbidden JSON column {forbidden!r} in source"


def test_signal_b_reuses_collect_verbatim(session_factory, policy) -> None:
    from app.services.handoff import HandoffMetrics

    now = utcnow()
    with session_factory() as session:
        _conv(session, "iso-a", "c1", at=now)
        _conv(session, "iso-a", "c2", at=now)
        _handoff(session, "iso-a", "c1", HandoffReason.NO_EVIDENCE.value, at=now)
        session.flush()
        metrics = HandoffMetrics(session, policy=policy).collect(_identity("iso-a"))
        reading = handoff_reading(session, _identity("iso-a"), policy=policy)
    assert reading.signal_id == "handoff_rate"
    assert reading.numerator == metrics["handoff_conversations"]
    assert reading.denominator == metrics["conversations"]
    assert reading.metric_value == metrics["handoff_rate"]


def test_signal_b_zero_denominator_passes_through_0_0(session_factory, policy) -> None:
    with session_factory() as session:
        reading = handoff_reading(session, _identity("iso-empty"), policy=policy)
    assert reading.denominator == 0
    assert reading.numerator == 0
    # collection layer must NOT convert 0.0 -> None; that null mapping is the exit layer
    assert reading.metric_value == 0.0
    assert reading.metric_value is not None


def test_signal_c_settled_and_pending(session_factory) -> None:
    now = utcnow()
    with session_factory() as session:
        _conv(session, "iso-a", "c1")
        _task(session, "iso-a", "t1")
        _tool_exec(session, "t1", "e1", ToolExecutionStatus.FAILED, at=now)
        _tool_exec(session, "t1", "e2", ToolExecutionStatus.FAILED, at=now)
        _tool_exec(session, "t1", "e3", ToolExecutionStatus.SENT, at=now)
        _tool_exec(session, "t1", "e4", ToolExecutionStatus.RECONCILED, at=now)
        _tool_exec(session, "t1", "e5", ToolExecutionStatus.CANCELLED, at=now)
        _tool_exec(session, "t1", "e6", ToolExecutionStatus.UNKNOWN, at=now)
        _tool_exec(session, "t1", "e7", ToolExecutionStatus.PENDING, at=now)
        session.flush()
        reading = tool_failure_reading(session, _identity("iso-a"))
    assert reading.signal_id == "tool_failure_rate"
    assert reading.numerator == 2  # only FAILED
    assert reading.denominator == 6  # all settled; PENDING excluded on both sides
    assert reading.metric_value == round(2 / 6, 4)


def test_signal_c_window_boundary(session_factory) -> None:
    now = utcnow()
    old = now - timedelta(minutes=120)
    with session_factory() as session:
        _task(session, "iso-a", "t1")
        _tool_exec(session, "t1", "e1", ToolExecutionStatus.FAILED, at=now)
        _tool_exec(session, "t1", "e2", ToolExecutionStatus.FAILED, at=old)
        _tool_exec(session, "t1", "e3", ToolExecutionStatus.SENT, at=now)
        session.flush()
        reading = tool_failure_reading(session, _identity("iso-a"), window_minutes=60)
    assert reading.numerator == 1
    assert reading.denominator == 2


def test_a_c_zero_denominator_is_none(session_factory) -> None:
    with session_factory() as session:
        a = no_evidence_reading(session, _identity("iso-none"))
        c = tool_failure_reading(session, _identity("iso-none"))
    assert a.denominator == 0 and a.metric_value is None
    assert c.denominator == 0 and c.metric_value is None


def test_cross_tenant_isolation_signal_a(session_factory) -> None:
    now = utcnow()
    with session_factory() as session:
        _conv(session, "iso-a", "ca")
        _msg(session, "ca", role="user", intent="POLICY_QA", at=now)
        _handoff(session, "iso-a", "ca", HandoffReason.NO_EVIDENCE.value, at=now)
        # tenant b has much more data
        _conv(session, "iso-b", "cb")
        for _ in range(5):
            _msg(session, "cb", role="user", intent="POLICY_QA", at=now)
        _handoff(session, "iso-b", "cb", HandoffReason.NO_EVIDENCE.value, at=now)
        session.flush()
        reading = no_evidence_reading(session, _identity("iso-a"))
    assert reading.numerator == 1
    assert reading.denominator == 1


def test_cross_tenant_isolation_signal_b(session_factory, policy) -> None:
    now = utcnow()
    with session_factory() as session:
        _conv(session, "iso-a", "ca", at=now)
        _conv(session, "iso-b", "cb1", at=now)
        _conv(session, "iso-b", "cb2", at=now)
        _handoff(session, "iso-b", "cb1", HandoffReason.NO_EVIDENCE.value, at=now)
        session.flush()
        reading = handoff_reading(session, _identity("iso-a"), policy=policy)
    assert reading.denominator == 1  # only iso-a's conversation
    assert reading.numerator == 0  # iso-b's handoff does not leak


def test_cross_tenant_isolation_signal_c(session_factory) -> None:
    now = utcnow()
    with session_factory() as session:
        _task(session, "iso-a", "ta")
        _task(session, "iso-b", "tb")
        _tool_exec(session, "ta", "ea", ToolExecutionStatus.FAILED, at=now)
        _tool_exec(session, "tb", "eb1", ToolExecutionStatus.FAILED, at=now)
        _tool_exec(session, "tb", "eb2", ToolExecutionStatus.FAILED, at=now)
        session.flush()
        reading = tool_failure_reading(session, _identity("iso-a"))
    assert reading.numerator == 1
    assert reading.denominator == 1


def test_collect_readings_order_and_vocabulary(session_factory, policy) -> None:
    with session_factory() as session:
        readings = collect_readings(session, _identity("iso-a"), policy=policy)
    assert [r.signal_id for r in readings] == list(SIGNAL_IDS)
    assert len(readings) == len(SIGNAL_IDS)
    table = get_online_signals()
    for reading in readings:
        table.by_id(reading.signal_id)  # resolvable against YAML vocabulary, no KeyError
