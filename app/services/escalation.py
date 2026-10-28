"""Escalation decisions: when the agent stops serving and reaches for a human.

The trigger table (``app/agent/escalation.yaml``) says which signals exist;
this module decides whether one of them fires for the turn in front of it.
The thresholds it compares against come from the policy engine, so a
deployment can grey-release a trigger by changing configuration.

Two phases, because some signals can only be judged once the action ran:

``pre``  signals that read the message and the conversation state
         (explicit request, complaint, frustration, risk, idle timeout);
``post`` signals that read the outcome (repeated failure, repeated
         clarification, missing evidence).

The rules are uniform: a signal names a *counter* and a *threshold*, and it
fires when the counter reached the threshold. A cue signal also fires the
moment the cue shows up, so one angry sentence is enough without waiting for
the counter.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from app.agent.escalation import EscalationCatalog, EscalationSignal
from app.agent.handoff import SentimentSnapshot
from app.core.policy import EvidenceScore, PolicyEngine
from app.models.enums import RiskLevel

__all__ = [
    "EscalationDecision",
    "EscalationEngine",
    "EscalationState",
]

Phase = Literal["pre", "post"]

COUNTER_NAMES = (
    "failed_intents",
    "clarification_count",
    "negative_rounds",
    "idle_minutes",
)


@dataclass(frozen=True, slots=True)
class EscalationState:
    """What the conversation looks like to the trigger table."""

    failed_intents: dict[str, int]
    clarification_count: int
    negative_rounds: int
    idle_minutes: int
    amount_cents: int | None = None

    def failures_for(self, intent: str) -> int:
        return self.failed_intents.get(intent, 0)

    def counter(self, name: str, intent: str = "") -> int:
        """The value of one named counter."""
        if name == "failed_intents":
            return self.failures_for(intent)
        if name == "clarification_count":
            return self.clarification_count
        if name == "negative_rounds":
            return self.negative_rounds
        if name == "idle_minutes":
            return self.idle_minutes
        raise KeyError(f"Unknown escalation counter: {name}.")


@dataclass(frozen=True, slots=True)
class EscalationDecision:
    """A signal that fired, and why."""

    signal: EscalationSignal
    detail: str

    @property
    def signal_id(self) -> str:
        return self.signal.signal_id

    @property
    def takeover(self) -> bool:
        return self.signal.takeover

    @property
    def continue_task(self) -> bool:
        return self.signal.continue_task


class EscalationEngine:
    """Decides which signal, if any, applies to the current turn."""

    def __init__(
        self, catalog: EscalationCatalog, policy: PolicyEngine
    ) -> None:
        self.catalog = catalog
        self.policy = policy

    # ------------------------------------------------------------- counters
    def count_message(self, text: str, state: EscalationState) -> EscalationState:
        """The state after the cues this message carries are counted."""
        if self._negative_cues(text):
            return replace(state, negative_rounds=state.negative_rounds + 1)
        return state

    def count_outcome(
        self,
        *,
        intent: str,
        state: EscalationState,
        clarified: bool,
        failed: bool,
    ) -> EscalationState:
        """The state after the outcome of the turn is counted.

        The clarification counter stops at its own budget: once the budget is
        spent the conversation is handed over, and a counter that kept climbing
        behind a silent agent would be a number nothing reads.
        """
        failures = dict(state.failed_intents)
        if failed:
            failures[intent] = failures.get(intent, 0) + 1
        budget = int(self.policy.escalation.threshold("clarification_rounds"))
        return replace(
            state,
            failed_intents=failures,
            clarification_count=min(
                state.clarification_count + (1 if clarified else 0), budget
            ),
        )

    # ------------------------------------------------------------ decisions
    def evaluate_pre(
        self,
        *,
        text: str,
        intent: str,
        risk_level: RiskLevel,
        state: EscalationState,
    ) -> EscalationDecision | None:
        """Signals that can be judged before the action runs."""
        return self._evaluate(
            "pre",
            text=text,
            intent=intent,
            risk_level=risk_level,
            state=state,
            evidence=None,
        )

    def evaluate_post(
        self,
        *,
        intent: str,
        state: EscalationState,
        evidence: EvidenceScore | None,
    ) -> EscalationDecision | None:
        """Signals that need the outcome of the action."""
        return self._evaluate(
            "post",
            text="",
            intent=intent,
            risk_level=None,
            state=state,
            evidence=evidence,
        )

    def sentiment(self, text: str, state: EscalationState) -> SentimentSnapshot:
        """How the user sounded, from the cues the trigger table declares."""
        cues = self._negative_cues(text)
        if not cues:
            return SentimentSnapshot(label="NEUTRAL", score=0.0)
        return SentimentSnapshot(
            label="NEGATIVE",
            score=-min(1.0, 0.5 * state.negative_rounds),
            signals=tuple(cues),
        )

    # ------------------------------------------------------------- internals
    def _evaluate(
        self,
        phase: Phase,
        *,
        text: str,
        intent: str,
        risk_level: RiskLevel | None,
        state: EscalationState,
        evidence: EvidenceScore | None,
    ) -> EscalationDecision | None:
        for signal in self.catalog.ordered():
            if signal.phase != phase:
                continue
            if not signal.applies_to(intent):
                continue
            decision = self._judge(
                signal,
                text=text,
                intent=intent,
                risk_level=risk_level,
                state=state,
                evidence=evidence,
            )
            if decision is not None:
                return decision
        return None

    def _judge(
        self,
        signal: EscalationSignal,
        *,
        text: str,
        intent: str,
        risk_level: RiskLevel | None,
        state: EscalationState,
        evidence: EvidenceScore | None,
    ) -> EscalationDecision | None:
        trigger = signal.trigger
        if trigger == "intent":
            if intent in signal.intents:
                return EscalationDecision(signal, f"intent={intent}")
            return None
        if trigger == "cue":
            hit = self._cue_hit(signal, text)
            if hit is not None:
                return EscalationDecision(signal, f"cue={hit}")
            if self._counter_reached(signal, intent, state):
                return EscalationDecision(
                    signal,
                    f"{signal.counter}={state.counter(signal.counter or '', intent)}",
                )
            return None
        if trigger == "threshold":
            if self._counter_reached(signal, intent, state):
                return EscalationDecision(
                    signal,
                    f"{signal.counter}={state.counter(signal.counter or '', intent)}",
                )
            return None
        if trigger == "risk_level":
            if (
                risk_level is not None
                and signal.risk_level == risk_level.value
                and self._needs_review(state)
            ):
                return EscalationDecision(
                    signal,
                    f"risk={risk_level.value},amount={state.amount_cents}",
                )
            return None
        if trigger == "no_evidence":
            if self._below_evidence(signal, evidence):
                detail = (
                    "evidence_score=None"
                    if evidence is None
                    else f"evidence_score={evidence.score},evidence_kind={evidence.kind}"
                )
                return EscalationDecision(signal, detail)
            return None
        return None

    def _cue_hit(self, signal: EscalationSignal, text: str) -> str | None:
        for cue in signal.cues:
            if cue in text:
                return cue
        return None

    def _negative_cues(self, text: str) -> list[str]:
        """The cues the table declares as a negative signal."""
        cues: list[str] = []
        for signal in self.catalog.signals:
            if signal.counter != "negative_rounds":
                continue
            hit = self._cue_hit(signal, text)
            if hit is not None and hit not in cues:
                cues.append(hit)
        return cues

    def _counter_reached(
        self, signal: EscalationSignal, intent: str, state: EscalationState
    ) -> bool:
        if signal.counter is None or signal.threshold is None:
            return False
        value = state.counter(signal.counter, intent)
        return value >= int(self.policy.escalation.threshold(signal.threshold))

    def _needs_review(self, state: EscalationState) -> bool:
        """A high-risk write needs a human when the policy asks for approval."""
        if state.amount_cents is None:
            return False
        return self.policy.requires_approval(state.amount_cents)

    def _below_evidence(
        self, signal: EscalationSignal, evidence: EvidenceScore | None
    ) -> bool:
        """True when the evidence is missing or below the line for its scale.

        Each scale carries its own limit: a BM25 score in the tens and a cosine
        score in 0..1 cannot share one number. ``signal.threshold`` no longer
        picks the value — it only names the family this trigger reads, and a
        typo there still has to fail loudly.
        """
        family = signal.threshold or "no_evidence_score"
        self.policy.escalation.threshold(family)
        if evidence is None:
            return True
        return evidence.score < self.policy.escalation.no_evidence_threshold(
            evidence.kind
        )
