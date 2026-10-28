"""The context package a human takes over with.

A handoff that only says "the user asked for a human" makes the user repeat
themselves. The bundle is what the seat sees on the first screen: what the
user wanted, what the agent already tried, which slots are known, which
policy clauses were cited and what is still in flight.

The bundle is a frozen value object. It is built once, stored with the
handoff event and served verbatim, so the seat and the audit trail can never
disagree about what was handed over.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.enums import Channel, HandoffReason

__all__ = [
    "ActionRecord",
    "CandidateOrder",
    "Citation",
    "HandoffBundle",
    "IntentRecord",
    "SentimentSnapshot",
]


@dataclass(frozen=True, slots=True)
class IntentRecord:
    """One turn of the conversation: what the user wanted and how it ended."""

    turn: int
    intent: str
    user_message: str
    outcome: str
    at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "intent": self.intent,
            "user_message": self.user_message,
            "outcome": self.outcome,
            "at": self.at,
        }


@dataclass(frozen=True, slots=True)
class ActionRecord:
    """An action the agent already ran, so the seat does not run it twice."""

    action: str
    intent: str
    result: str
    ok: bool
    at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "intent": self.intent,
            "result": self.result,
            "ok": self.ok,
            "at": self.at,
        }


@dataclass(frozen=True, slots=True)
class CandidateOrder:
    """An order the customer was shown as a candidate and did not pick.

    The same four L1 facts as the card, so the seat can carry on from the list
    the customer was already looking at instead of asking for a number again.
    """

    order_id: str
    product_name: str
    status: str
    delivered_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "product_name": self.product_name,
            "status": self.status,
            "delivered_at": self.delivered_at,
        }


@dataclass(frozen=True, slots=True)
class Citation:
    """A policy clause the agent already cited."""

    document_id: str
    title: str
    version: str
    section: str | None = None
    source_uri: str | None = None
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "version": self.version,
            "section": self.section,
            "source_uri": self.source_uri,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class SentimentSnapshot:
    """How the user sounded, so the seat knows which tone to use."""

    label: str
    score: float
    signals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "score": self.score,
            "signals": list(self.signals),
        }


@dataclass(frozen=True, slots=True)
class HandoffBundle:
    """Everything the seat needs, and nothing the seat has to ask for."""

    conversation_id: str
    tenant_id: str
    user_id: str
    channel: Channel
    reason: HandoffReason
    signal_id: str
    priority: str
    queue: str
    transcript_digest: str
    transcript_ref: str
    intent_history: tuple[IntentRecord, ...]
    slots: dict[str, Any] = field(default_factory=dict)
    attempted_actions: tuple[ActionRecord, ...] = ()
    attempted_candidates: tuple[CandidateOrder, ...] = ()
    pending_task_id: str | None = None
    sentiment: SentimentSnapshot = field(
        default_factory=lambda: SentimentSnapshot(label="NEUTRAL", score=0.0)
    )
    policy_refs: tuple[Citation, ...] = ()
    suggested_next_step: str = ""
    created_at: str = ""
    # What the customer wrote about themselves before this conversation, in
    # their own words. It is here so the seat does not ask again; it is
    # masked, clipped and sourced, and it exists only in this package.
    history_summary: str | None = None
    history_summary_source: str = ""
    history_summary_max_chars: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "conversation_id": self.conversation_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "channel": self.channel.value,
            "reason": self.reason.value,
            "signal_id": self.signal_id,
            "priority": self.priority,
            "queue": self.queue,
            "transcript_digest": self.transcript_digest,
            "transcript_ref": self.transcript_ref,
            "intent_history": [record.to_dict() for record in self.intent_history],
            "slots": dict(self.slots),
            "attempted_actions": [record.to_dict() for record in self.attempted_actions],
            "attempted_candidates": [
                candidate.to_dict() for candidate in self.attempted_candidates
            ],
            "pending_task_id": self.pending_task_id,
            "sentiment": self.sentiment.to_dict(),
            "policy_refs": [citation.to_dict() for citation in self.policy_refs],
            "suggested_next_step": self.suggested_next_step,
            "created_at": self.created_at,
        }
        # Nothing to say means nothing written: no null, no empty string, no
        # zero. A seat must be able to tell "there is no summary" from
        # "the summary is empty".
        if self.history_summary:
            payload["history_summary"] = self.history_summary
            payload["history_summary_source"] = self.history_summary_source
            payload["history_summary_max_chars"] = self.history_summary_max_chars
        return payload
