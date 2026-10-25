from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base
from app.models.enums import LogisticsStatus, OrderStatus, RefundStatus


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, native_enum=False, length=32, validate_strings=True)
    )
    total_cents: Mapped[int] = mapped_column(Integer)
    refundable_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    paid_at: Mapped[datetime] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Logistics(Base):
    __tablename__ = "logistics"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("orders.id", ondelete="CASCADE"), unique=True
    )
    carrier: Mapped[str] = mapped_column(String(64))
    tracking_no: Mapped[str] = mapped_column(String(64))
    status: Mapped[LogisticsStatus] = mapped_column(
        SAEnum(LogisticsStatus, native_enum=False, length=32, validate_strings=True)
    )
    last_event: Mapped[str] = mapped_column(String(200))
    estimated_delivery: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_refund_idempotency"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[RefundStatus] = mapped_column(
        SAEnum(RefundStatus, native_enum=False, length=32, validate_strings=True)
    )
    provider: Mapped[str] = mapped_column(String(32), default="mock")
    external_refund_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
