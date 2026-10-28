"""The policy engine: every threshold the agent obeys, in one place.

The platform plan's second gate says thresholds must come from configuration,
not from code. This module is that configuration's single reader: services ask
the engine instead of reading ``Settings`` or hard-coding a number.

The engine is immutable and versioned. ``version`` travels with every policy
decision, so an approval created under one set of thresholds cannot be
confused with an approval created under another.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings

__all__ = [
    "EVIDENCE_KINDS",
    "EscalationThresholds",
    "EvidenceScore",
    "PolicyEngine",
    "RefundThresholds",
]

# The scales an evidence score can be on. Written out as literals on purpose:
# this module is tier 0 and ``app/retrieval`` is tier 2, so importing
# ``SCORE_KINDS`` from there would be an upward dependency the architecture
# guard rejects. ``app/retrieval/search.py`` still owns the retrieval half of
# the vocabulary; a test pins the two against each other.
EVIDENCE_KINDS: tuple[str, ...] = ("sparse", "dense", "fusion", "rerank", "verdict")

# A verdict score is the action layer's own 0-or-1 answer, not a retrieval
# score. 0.5 is the only meaningful line through it, so it takes no setting.
_VERDICT_NO_EVIDENCE_THRESHOLD = 0.5


@dataclass(frozen=True, slots=True)
class EvidenceScore:
    """A score together with the scale it was measured on."""

    score: float
    kind: str


@dataclass(frozen=True, slots=True)
class RefundThresholds:
    """The rules a refund must satisfy."""

    window_days: int
    max_amount_cents: int
    approval_threshold_cents: int


@dataclass(frozen=True, slots=True)
class EscalationThresholds:
    """When the agent stops trying and reaches for a human."""

    frustration_rounds: int
    clarification_rounds: int
    after_failed_attempts: int
    no_evidence_score: float
    no_evidence_score_dense: float
    no_evidence_score_fusion: float
    no_evidence_score_rerank: float
    session_idle_minutes: int
    claim_timeout_seconds: int
    repeat_window_hours: int
    abandon_idle_minutes: int
    digest_max_chars: int

    def threshold(self, name: str) -> float:
        """Resolve a threshold named by the escalation table."""
        values = {
            "frustration_rounds": float(self.frustration_rounds),
            "clarification_rounds": float(self.clarification_rounds),
            "after_failed_attempts": float(self.after_failed_attempts),
            "no_evidence_score": self.no_evidence_score,
            "session_idle_minutes": float(self.session_idle_minutes),
        }
        if name not in values:
            raise KeyError(f"Unknown escalation threshold: {name}.")
        return values[name]

    def no_evidence_threshold(self, kind: str) -> float:
        """The "too weak to answer" line for the scale this score is on.

        A BM25 score in the tens and a cosine score in 0..1 are not the same
        kind of number, so one shared limit would be wrong on at least one of
        them. An unknown kind is a programming error, never a fall back to the
        sparse line.
        """
        thresholds = {
            "sparse": self.no_evidence_score,
            "dense": self.no_evidence_score_dense,
            "fusion": self.no_evidence_score_fusion,
            "rerank": self.no_evidence_score_rerank,
            "verdict": _VERDICT_NO_EVIDENCE_THRESHOLD,
        }
        if kind not in thresholds:
            raise KeyError(f"Unknown evidence score kind: {kind}.")
        return thresholds[kind]


@dataclass(frozen=True, slots=True)
class PolicyEngine:
    """Read-only view of the configured policy."""

    version: str
    refund: RefundThresholds
    escalation: EscalationThresholds

    @classmethod
    def from_settings(cls, settings: Settings) -> PolicyEngine:
        return cls(
            version=settings.refund_policy_version,
            refund=RefundThresholds(
                window_days=settings.refund_window_days,
                max_amount_cents=settings.refund_max_amount_cents,
                approval_threshold_cents=settings.refund_approval_threshold_cents,
            ),
            escalation=EscalationThresholds(
                frustration_rounds=settings.escalation_frustration_rounds,
                clarification_rounds=settings.escalation_clarification_rounds,
                after_failed_attempts=settings.handoff_after_failed_attempts,
                no_evidence_score=settings.escalation_no_evidence_score,
                no_evidence_score_dense=settings.escalation_no_evidence_score_dense,
                no_evidence_score_fusion=settings.escalation_no_evidence_score_fusion,
                no_evidence_score_rerank=settings.escalation_no_evidence_score_rerank,
                session_idle_minutes=settings.session_idle_minutes,
                claim_timeout_seconds=settings.handoff_claim_timeout_seconds,
                repeat_window_hours=settings.handoff_repeat_window_hours,
                abandon_idle_minutes=settings.abandon_idle_minutes,
                digest_max_chars=settings.handoff_digest_max_chars,
            ),
        )

    # ------------------------------------------------------------- refunds
    def requires_approval(self, amount_cents: int) -> bool:
        return amount_cents > self.refund.approval_threshold_cents

    def exceeds_refund_cap(self, amount_cents: int) -> bool:
        return amount_cents > self.refund.max_amount_cents

    def within_return_window(self, delivered_at: datetime, now: datetime) -> bool:
        return now - delivered_at <= timedelta(days=self.refund.window_days)

    # ---------------------------------------------------------- escalation
    def should_escalate(self, *, failed_attempts: int, frustrated_rounds: int) -> bool:
        """True when the agent has tried enough times to hand over."""
        return (
            failed_attempts >= self.escalation.after_failed_attempts
            or frustrated_rounds >= self.escalation.frustration_rounds
        )
