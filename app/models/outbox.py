from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base


class OutboxMessage(Base):
    """One message that has to reach the outside world.

    The row is written in the same transaction as the state change that
    caused it, so a message cannot be lost by a crash between the two. A
    worker claims the row, pushes it and marks it sent; a push that fails
    is retried with a backoff, and the row that never lands becomes the
    dead letter.

    ``dedupe_key`` is what makes the write idempotent: the same business
    fact enqueued twice is one row, not two notifications.
    """

    __tablename__ = "outbox_messages"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_outbox_messages_dedupe_key"),
        Index("ix_outbox_messages_due", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(48))
    aggregate_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(160))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
