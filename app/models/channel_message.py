from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base


class ChannelMessage(Base):
    """One inbound delivery, keyed by the provider's message id.

    The unique key is what makes a retried webhook exactly-once from the
    customer's point of view: the second delivery reads the stored reply
    instead of running the turn again. ``request_digest`` catches a reused
    id with different content, which is a client bug or a replay attempt.
    """

    __tablename__ = "channel_messages"
    __table_args__ = (
        UniqueConstraint(
            "channel",
            "channel_message_id",
            name="uq_channel_messages_delivery",
        ),
        Index("ix_channel_messages_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    channel: Mapped[str] = mapped_column(String(16), index=True)
    channel_message_id: Mapped[str] = mapped_column(String(160))
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )
    direction: Mapped[str] = mapped_column(String(8), default="INBOUND")
    status: Mapped[str] = mapped_column(String(16), default="PROCESSING", index=True)
    request_digest: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    rendered_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
