"""A model conclusion the task ledger may reuse, but never trust.

The chat entry asks the model when the rules cannot read a sentence. The write
path that follows must not pay for the same question twice, yet it also must
not accept the entry's verdict on faith. This record is the middle ground: it
carries enough to *recompute the decision* (request digest, rule fingerprint,
rule reading, model reading, model name) so the ledger can verify the entry saw
the same request under the same rules, and only then reuse the model's answer.

The record is passed in memory. It is never written into ``tasks.request`` or
``tasks.context``: a stored verdict would let the entry control the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.routing import RoutePlan
from app.services.approvals import canonical_digest

__all__ = ["ModelDecision"]


def request_digest(request: str, context: dict[str, Any] | None) -> str:
    """The same digest the idempotency key uses for the same pair."""
    return canonical_digest({"request": request, "context": context or {}})


@dataclass(frozen=True, slots=True)
class ModelDecision:
    """What the model read, and the conditions under which it may be reused."""

    digest: str
    ruleset: str
    model_name: str | None
    rule_intent: str
    rule_order_id: str | None
    rule_amount_cents: int | None
    model_intent: str
    model_order_id: str | None
    model_amount_cents: int | None
    trace: dict[str, Any]

    def verifies(
        self,
        *,
        request: str,
        context: dict[str, Any] | None,
        rule_plan: RoutePlan,
        ruleset: str,
        model_name: str | None,
    ) -> bool:
        """True only when every input the model saw is still the current one."""
        return (
            self.digest == request_digest(request, context)
            and self.ruleset == ruleset
            and self.model_name == model_name
            and self.rule_intent == rule_plan.intent.value
            and self.rule_order_id == rule_plan.order_id
            and self.rule_amount_cents == rule_plan.amount_cents
        )
