from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.agent.escalation import get_escalation_catalog
from app.core.config import Settings
from app.core.policy import EVIDENCE_KINDS, EvidenceScore, PolicyEngine
from app.core.time import utcnow
from app.retrieval.search import SCORE_KINDS
from app.services.escalation import EscalationEngine, EscalationState


def _engine(**overrides: object) -> PolicyEngine:
    values: dict[str, object] = {"environment": "test"}
    values.update(overrides)
    return PolicyEngine.from_settings(Settings(**values))


def _escalation(engine: PolicyEngine) -> EscalationEngine:
    return EscalationEngine(get_escalation_catalog(), engine)


def _quiet_state() -> EscalationState:
    return EscalationState(
        failed_intents={},
        clarification_count=0,
        negative_rounds=0,
        idle_minutes=0,
    )


def test_the_engine_reads_its_thresholds_from_configuration() -> None:
    engine = _engine(
        refund_window_days=14,
        refund_max_amount_cents=100_000,
        refund_approval_threshold_cents=50_000,
        escalation_frustration_rounds=3,
        handoff_after_failed_attempts=4,
    )

    assert engine.refund.window_days == 14
    assert engine.refund.max_amount_cents == 100_000
    assert engine.refund.approval_threshold_cents == 50_000
    assert engine.escalation.frustration_rounds == 3
    assert engine.escalation.after_failed_attempts == 4


def test_the_policy_version_travels_with_the_decision() -> None:
    engine = _engine(refund_policy_version="refund-policy-v9")

    assert engine.version == "refund-policy-v9"


def test_the_approval_threshold_decides_when_a_human_is_needed() -> None:
    engine = _engine(refund_approval_threshold_cents=50_000)

    assert engine.requires_approval(50_001) is True
    assert engine.requires_approval(50_000) is False


def test_the_refund_cap_is_enforced() -> None:
    engine = _engine(refund_max_amount_cents=100_000)

    assert engine.exceeds_refund_cap(100_001) is True
    assert engine.exceeds_refund_cap(100_000) is False


def test_the_return_window_is_measured_in_configured_days() -> None:
    engine = _engine(refund_window_days=14)
    now = utcnow()

    assert engine.within_return_window(now - timedelta(days=10), now) is True
    assert engine.within_return_window(now - timedelta(days=20), now) is False


def test_escalation_happens_after_the_configured_attempts() -> None:
    engine = _engine(handoff_after_failed_attempts=2, escalation_frustration_rounds=3)

    assert engine.should_escalate(failed_attempts=1, frustrated_rounds=0) is False
    assert engine.should_escalate(failed_attempts=2, frustrated_rounds=0) is True
    assert engine.should_escalate(failed_attempts=0, frustrated_rounds=3) is True


def test_the_clarification_budget_is_its_own_threshold() -> None:
    """追问预算与挫败度分开：改一个不该顺带改掉另一个。"""
    default = _engine()
    tuned = _engine(escalation_clarification_rounds=4, escalation_frustration_rounds=2)

    assert default.escalation.clarification_rounds == 3
    assert default.escalation.threshold("clarification_rounds") == 3
    assert tuned.escalation.threshold("clarification_rounds") == 4
    assert tuned.escalation.threshold("frustration_rounds") == 2


def test_a_configured_window_changes_the_runtime_answer(
    app_factory,  # type: ignore[no-untyped-def]
) -> None:
    tight = app_factory(refund_window_days=1)
    loose = app_factory(refund_window_days=3650)

    assert _eligibility(tight) is False
    assert _eligibility(loose) is True


def test_a_configured_cap_blocks_an_oversized_refund(
    app_factory,  # type: ignore[no-untyped-def]
) -> None:
    capped = app_factory(refund_max_amount_cents=1)

    response = TestClient(capped).post(
        "/api/v1/tasks",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "SUCCEEDED"
    assert body["result"]["outcome"] == "FAILED"
    assert "AMOUNT_EXCEEDS_POLICY_CAP" in str(body["result"]["checks"])


def _eligibility(app) -> bool:  # type: ignore[no-untyped-def]
    """Ask the read-only agent whether the seeded order can be refunded."""
    client = TestClient(app)
    response = client.post(
        "/api/v1/support/messages",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={"message": "订单 ORD-DEMO-1001 可以退款吗"},
    )
    assert response.status_code == 200
    reply = response.json()["reply"]
    return "不符合退款条件" not in reply


# ------------------------------------------------- no evidence, per scale
def test_the_no_evidence_line_is_judged_on_the_scale_of_the_score() -> None:
    """同一组数字,换个种类结论就相反:四把尺子不能共用一个阈值。"""
    engine = _engine()
    escalation = _escalation(engine)

    # The table itself, read directly, so a failure points at the number.
    assert engine.escalation.no_evidence_threshold("sparse") == 5.0
    assert engine.escalation.no_evidence_threshold("dense") == 0.35
    assert engine.escalation.no_evidence_threshold("fusion") == 0.0164
    assert engine.escalation.no_evidence_threshold("rerank") == 0.35

    # A BM25 score of 4.9 is weak on the sparse scale.
    sparse = escalation.evaluate_post(
        intent="POLICY_QA",
        state=_quiet_state(),
        evidence=EvidenceScore(score=4.9, kind="sparse"),
    )
    assert sparse is not None
    assert sparse.signal_id == "no_evidence"
    assert "evidence_kind=sparse" in sparse.detail

    # The same 0.4 would be weak on the BM25 scale but is strong as a cosine.
    dense = escalation.evaluate_post(
        intent="POLICY_QA",
        state=_quiet_state(),
        evidence=EvidenceScore(score=0.4, kind="dense"),
    )
    assert dense is None

    # An RRF score sits around 1/61, so 0.01 means neither channel ranked it.
    fusion = escalation.evaluate_post(
        intent="POLICY_QA",
        state=_quiet_state(),
        evidence=EvidenceScore(score=0.01, kind="fusion"),
    )
    assert fusion is not None
    assert fusion.signal_id == "no_evidence"
    assert "evidence_kind=fusion" in fusion.detail


def test_missing_evidence_still_hands_over_with_no_kind_at_all() -> None:
    """没有分数就没有尺子:这一支仍按无依据处理,不拿稀疏阈值去猜。"""
    decision = _escalation(_engine()).evaluate_post(
        intent="POLICY_QA", state=_quiet_state(), evidence=None
    )

    assert decision is not None
    assert decision.signal_id == "no_evidence"
    assert decision.detail == "evidence_score=None"


def test_an_unknown_score_kind_is_an_error_rather_than_a_sparse_fallback() -> None:
    engine = _engine()

    with pytest.raises(KeyError):
        engine.escalation.no_evidence_threshold("unknown")
    with pytest.raises(KeyError):
        engine.escalation.no_evidence_threshold("")


def test_the_threshold_table_covers_every_retrieval_score_kind() -> None:
    """core 只写字面量,所以用一条用例把两份词表钉在一起。"""
    engine = _engine()

    assert set(EVIDENCE_KINDS) == set(SCORE_KINDS) | {"verdict"}
    for kind in EVIDENCE_KINDS:
        assert isinstance(engine.escalation.no_evidence_threshold(kind), float)


def test_a_verdict_score_keeps_the_statistics_answers_where_they_were() -> None:
    """动作层自写的 0/1 哨兵换种类后,是否转人工的结论必须逐条不变。"""
    escalation = _escalation(_engine())

    answered = escalation.evaluate_post(
        intent="SELF_ANALYTICS",
        state=_quiet_state(),
        evidence=EvidenceScore(score=1.0, kind="verdict"),
    )
    assert answered is None

    refused = escalation.evaluate_post(
        intent="SELF_ANALYTICS",
        state=_quiet_state(),
        evidence=EvidenceScore(score=0.0, kind="verdict"),
    )
    assert refused is not None
    assert refused.signal_id == "no_evidence"


def test_each_scale_carries_its_own_setting() -> None:
    """三个新旋钮互不串味:改稠密那条不该动稀疏那条。"""
    engine = _engine(
        escalation_no_evidence_score_dense=0.9,
        escalation_no_evidence_score_fusion=0.5,
        escalation_no_evidence_score_rerank=0.7,
    )

    assert engine.escalation.no_evidence_threshold("dense") == 0.9
    assert engine.escalation.no_evidence_threshold("fusion") == 0.5
    assert engine.escalation.no_evidence_threshold("rerank") == 0.7
    assert engine.escalation.no_evidence_threshold("sparse") == 5.0

