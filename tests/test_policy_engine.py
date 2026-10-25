from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.policy import PolicyEngine
from app.core.time import utcnow


def _engine(**overrides: object) -> PolicyEngine:
    values: dict[str, object] = {"environment": "test"}
    values.update(overrides)
    return PolicyEngine.from_settings(Settings(**values))


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
        json={"request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元"},
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
