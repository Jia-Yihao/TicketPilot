from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.commerce import Logistics, Order, Refund
from app.models.enums import RefundStatus

BLOCKING_REFUND_STATUSES = (
    RefundStatus.REQUESTED,
    RefundStatus.SUCCEEDED,
    RefundStatus.UNKNOWN,
)


class CommerceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_order(self, order_id: str) -> Order | None:
        return self.session.get(Order, order_id)

    def get_order_for_update(self, order_id: str) -> Order | None:
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return self.session.scalars(stmt).first()

    def list_recent_orders(
        self, tenant_id: str, user_id: str, limit: int = 3
    ) -> list[Order]:
        """The caller's own newest orders — the only source a candidate list may use.

        Both columns are in the predicate and neither is relaxed: a support seat
        asking on someone else's behalf must not get that person's order numbers
        back as chips, because the list is rendered to whoever is typing.
        ``id`` breaks ties so two orders paid in the same second still come back
        in one fixed order.
        """
        stmt = (
            select(Order)
            .where(Order.tenant_id == tenant_id, Order.user_id == user_id)
            .order_by(Order.paid_at.desc(), Order.id.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def apply_refund_balance(self, order_id: str, amount_cents: int) -> bool:
        stmt = (
            update(Order)
            .where(
                Order.id == order_id,
                Order.refundable_cents >= amount_cents,
            )
            .values(
                refundable_cents=Order.refundable_cents - amount_cents,
                version=Order.version + 1,
                updated_at=utcnow(),
            )
            .returning(Order.id)
            .execution_options(synchronize_session=False)
        )
        return self.session.execute(stmt).scalar_one_or_none() is not None

    def get_logistics(self, order_id: str) -> Logistics | None:
        stmt = select(Logistics).where(Logistics.order_id == order_id)
        return self.session.scalars(stmt).first()

    def list_refunds(self, order_id: str) -> list[Refund]:
        stmt = select(Refund).where(Refund.order_id == order_id).order_by(Refund.created_at)
        return list(self.session.scalars(stmt))

    def blocking_refund(self, order_id: str) -> Refund | None:
        stmt = select(Refund).where(
            Refund.order_id == order_id,
            Refund.status.in_(BLOCKING_REFUND_STATUSES),
        )
        return self.session.scalars(stmt).first()

    def blocking_refund_except(
        self, order_id: str, idempotency_key: str
    ) -> Refund | None:
        stmt = select(Refund).where(
            Refund.order_id == order_id,
            Refund.status.in_(BLOCKING_REFUND_STATUSES),
            Refund.idempotency_key != idempotency_key,
        )
        return self.session.scalars(stmt).first()

    def get_refund_by_idempotency(self, idempotency_key: str) -> Refund | None:
        stmt = select(Refund).where(Refund.idempotency_key == idempotency_key)
        return self.session.scalars(stmt).first()

    def create_refund(self, refund: Refund) -> Refund:
        self.session.add(refund)
        self.session.flush()
        return refund
