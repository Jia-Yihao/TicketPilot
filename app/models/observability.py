from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base


class TraceSpan(Base):
    """One step of a request: understand, retrieve, tool, approval, handoff.

    A span answers a single question about the run — what was understood,
    what was retrieved, what was called, who intervened — so a request can be
    replayed from the trace id alone.
    """

    __tablename__ = "trace_spans"
    __table_args__ = (
        Index("ix_trace_spans_trace_started", "trace_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(40), index=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="OK")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    ended_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
