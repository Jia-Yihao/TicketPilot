from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base


class SecurityEvent(Base):
    """One observed guard hit, before or after the customer's turn.

    The row is evidence, not a verdict: ``action`` says what the edge did
    (log, redact or block), and ``excerpt`` is already masked. The raw message
    stays in the conversation and is never copied here.
    """

    __tablename__ = "security_events"
    __table_args__ = (
        Index("ix_security_events_tenant_created", "tenant_id", "created_at"),
        Index("ix_security_events_tenant_category", "tenant_id", "category"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )
    trace_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="MEDIUM", nullable=False)
    action: Mapped[str] = mapped_column(String(16), default="LOGGED", nullable=False)
    rule_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="input", nullable=False)
    excerpt: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
