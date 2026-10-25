from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.money import format_cents
from app.core.security import Identity
from app.models.commerce import Logistics, Order
from app.repositories.commerce import CommerceRepository


class OrderService:
    def __init__(self, session: Session) -> None:
        self.repo = CommerceRepository(session)

    def require_order(self, identity: Identity, order_id: str) -> Order:
        order = self.repo.get_order(order_id)
        if order is None or order.tenant_id != identity.tenant_id:
            raise NotFoundError("Order not found.")
        if order.user_id != identity.user_id and not identity.has_role("support"):
            raise NotFoundError("Order not found.")
        return order

    def get_logistics(self, identity: Identity, order_id: str) -> Logistics | None:
        order = self.require_order(identity, order_id)
        return self.repo.get_logistics(order.id)

    def order_view(self, order: Order) -> dict[str, Any]:
        return {
            "order_id": order.id,
            "status": order.status.value,
            "total_cents": order.total_cents,
            "refundable_cents": order.refundable_cents,
            "currency": order.currency,
            "paid_at": order.paid_at.isoformat(),
            "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
            "version": order.version,
        }

    def order_summary(self, order: Order) -> str:
        return (
            f"订单 {order.id} 当前状态为 {order.status.value}，"
            f"订单金额 {format_cents(order.total_cents)} {order.currency}，"
            f"可退余额 {format_cents(order.refundable_cents)} {order.currency}。"
        )

    def logistics_view(self, order: Order, logistics: Logistics | None) -> dict[str, Any]:
        if logistics is None:
            return {"order_id": order.id, "available": False}
        return {
            "order_id": order.id,
            "available": True,
            "carrier": logistics.carrier,
            "tracking_no": logistics.tracking_no,
            "status": logistics.status.value,
            "last_event": logistics.last_event,
            "estimated_delivery": (
                logistics.estimated_delivery.isoformat() if logistics.estimated_delivery else None
            ),
            "updated_at": logistics.updated_at.isoformat(),
        }

    def logistics_summary(self, order: Order, logistics: Logistics | None) -> str:
        if logistics is None:
            return f"订单 {order.id} 暂无物流记录。"
        return (
            f"订单 {order.id} 由 {logistics.carrier} 承运（运单 {logistics.tracking_no}），"
            f"最新状态 {logistics.status.value}：{logistics.last_event}。"
        )
