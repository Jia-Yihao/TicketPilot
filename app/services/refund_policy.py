from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.money import format_cents
from app.core.policy import PolicyEngine
from app.core.security import Identity
from app.core.time import utcnow
from app.models.commerce import Order
from app.models.enums import OrderStatus, RefundStatus
from app.repositories.commerce import CommerceRepository
from app.services.orders import OrderService


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    eligible: bool
    reasons: tuple[str, ...]
    amount_cents: int
    max_refundable_cents: int
    requires_approval: bool
    policy_version: str
    order: Order = field(repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "amount_cents": self.amount_cents,
            "max_refundable_cents": self.max_refundable_cents,
            "amount": format_cents(self.amount_cents),
            "currency": self.order.currency,
            "requires_approval": self.requires_approval,
            "policy_version": self.policy_version,
        }


class RefundPolicy:
    def __init__(self, session: Session, engine: PolicyEngine) -> None:
        self.repo = CommerceRepository(session)
        self.orders = OrderService(session)
        self.engine = engine

    def check(
        self,
        identity: Identity,
        order_id: str,
        requested_amount_cents: int | None = None,
        *,
        now: datetime | None = None,
    ) -> EligibilityResult:
        order = self.orders.require_order(identity, order_id)
        now = now or utcnow()
        reasons: list[str] = []

        if order.status is OrderStatus.CANCELLED:
            reasons.append("ORDER_CANCELLED")
        elif order.status not in {OrderStatus.SHIPPED, OrderStatus.DELIVERED}:
            reasons.append("ORDER_STATUS_NOT_REFUNDABLE")

        if order.status is OrderStatus.DELIVERED and order.delivered_at is not None:
            if not self.engine.within_return_window(order.delivered_at, now):
                reasons.append("RETURN_WINDOW_EXPIRED")

        if order.refundable_cents <= 0:
            reasons.append("NO_REFUNDABLE_BALANCE")

        blocking = self.repo.blocking_refund(order.id)
        if blocking is not None:
            if blocking.status is RefundStatus.SUCCEEDED:
                reasons.append("ALREADY_REFUNDED")
            elif blocking.status is RefundStatus.UNKNOWN:
                reasons.append("REFUND_STATUS_UNKNOWN")
            else:
                reasons.append("REFUND_ALREADY_REQUESTED")

        amount = (
            requested_amount_cents
            if requested_amount_cents is not None
            else order.refundable_cents
        )
        if amount <= 0:
            reasons.append("AMOUNT_INVALID")
        elif amount > order.refundable_cents:
            reasons.append("AMOUNT_EXCEEDS_REFUNDABLE")
        elif self.engine.exceeds_refund_cap(amount):
            reasons.append("AMOUNT_EXCEEDS_POLICY_CAP")

        return EligibilityResult(
            eligible=not reasons,
            reasons=tuple(reasons),
            amount_cents=amount,
            max_refundable_cents=order.refundable_cents,
            requires_approval=self.engine.requires_approval(amount),
            policy_version=self.engine.version,
            order=order,
        )
