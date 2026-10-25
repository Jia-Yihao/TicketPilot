from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base


class HandoffEvent(Base):
    """One escalation: why the agent stopped, and everything a seat needs.

    The row is both the audit trail of the escalation and the state of the
    takeover. ``bundle`` holds the full :class:`HandoffBundle` the seat sees
    on the first screen, so the seat never has to reconstruct the context.
    """

    __tablename__ = "handoff_events"
    __table_args__ = (
        Index("ix_handoff_events_tenant_status", "tenant_id", "status"),
        Index("ix_handoff_events_conversation", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    signal_id: Mapped[str] = mapped_column(String(48), index=True)
    reason: Mapped[str] = mapped_column(String(32), index=True)
    priority: Mapped[str] = mapped_column(String(8))
    queue: Mapped[str] = mapped_column(String(32), default="GENERAL")
    status: Mapped[str] = mapped_column(String(32), default="HANDOFF_PENDING", index=True)
    intent: Mapped[str | None] = mapped_column(String(48), nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    bundle: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    pending_task_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class AbandonEvent(Base):
    """A conversation that went silent without a resolution.

    Abandonment is the failure mode nobody notices: the user neither got an
    answer nor a human. Each row is one suspected abandonment, sliced by
    intent and channel so the rate can be watched per slice.
    """

    __tablename__ = "abandon_events"
    __table_args__ = (
        Index("ix_abandon_events_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    intent: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(16), default="WEB", index=True)
    reason: Mapped[str] = mapped_column(String(32), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idle_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
