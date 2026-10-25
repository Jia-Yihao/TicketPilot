from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base


class SqlQuery(Base):
    """One read-only question that was answered from the ledger.

    The row keeps the statement, not the values: every value is bound, so
    the text alone tells what was asked. The parameter values are reduced to
    a digest, because the owner's identifiers do not belong in a log.
    """

    __tablename__ = "sql_queries"
    __table_args__ = (
        Index("ix_sql_queries_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text)
    dataset: Mapped[str] = mapped_column(String(32))
    metric: Mapped[str] = mapped_column(String(32))
    group_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    row_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    time_range: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sql_text: Mapped[str] = mapped_column(Text)
    params_digest: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="SUCCEEDED")
    rejected_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
