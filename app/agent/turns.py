"""The conversation-level data types shared by the agent and its actions.

They live in their own module so the action handlers can import them without
importing the agent, which would be a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SupportIntent(StrEnum):
    """The read-path sub-labels of the intent catalog.

    These are labels for routing and wording; the capability behind a label
    is decided by the catalog row it points at.
    """

    PRODUCT_QUERY = "PRODUCT_QUERY"
    POLICY_QA = "POLICY_QA"
    ORDER_QUERY = "ORDER_QUERY"
    LOGISTICS_QUERY = "LOGISTICS_QUERY"
    REFUND_ELIGIBILITY = "REFUND_ELIGIBILITY"
    REFUND_STATUS = "REFUND_STATUS"
    DELIVERY_ISSUE = "DELIVERY_ISSUE"
    REFUND_REQUEST = "REFUND_REQUEST"
    TICKET_CREATE = "TICKET_CREATE"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def _missing_(cls, value: object) -> SupportIntent | None:
        """Accept a label the catalog introduced but this build does not know.

        A new catalog row is usable without editing the enum, so the catalog
        stays the only place an intent is declared.
        """
        if isinstance(value, str) and value:
            member = str.__new__(cls, value)
            member._name_ = value
            member._value_ = value
            cls._value2member_map_.setdefault(value, member)
            cls._member_map_.setdefault(value, member)
            return member
        return None


@dataclass(frozen=True, slots=True)
class SupportPlan:
    intent: SupportIntent
    order_id: str | None = None


@dataclass(slots=True)
class Draft:
    intent: SupportIntent
    reply: str
    cards: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    task: dict[str, Any] | None = None
    clarification: dict[str, Any] | None = None
    # How strong the evidence behind the answer was. ``None`` means the
    # action had no evidence to weigh (a lookup, not a retrieval).
    evidence_score: float | None = None
    # False when the turn left the user without an answer; the escalation
    # counters count these.
    resolved: bool = True
    # The action that produced the draft; the handoff bundle records it so a
    # seat does not run the same capability twice.
    action: str | None = None
    # The escalation this turn raised, if any.
    handoff: dict[str, Any] | None = None


@dataclass(slots=True)
class Turn:
    conversation_id: str
    order_id: str | None = None
    amount_cents: int | None = None
    product_id: str | None = None
    inherited: list[str] = field(default_factory=list)
