from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base


class LlmUsageRecord(Base):
    """One model run, written where every worker can read it.

    The in-process ``LlmUsage`` counter answers "what did this process spend"
    and dies with the worker. This row is the accounting fact: one row per
    planning call, with the retry count that validation produced and the
    tokens/latency the provider reported. ``source`` separates the task
    runtime from the chat gap path so the two budgets can be read apart.
    """

    __tablename__ = "llm_usage"
    __table_args__ = (
        Index("ix_llm_usage_tenant_created", "tenant_id", "created_at"),
        Index("ix_llm_usage_source_created", "source", "created_at"),
        Index("ix_llm_usage_task", "task_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    # "task" is the runtime planner; "chat" is the gap path in SupportAgent.
    source: Mapped[str] = mapped_column(String(16), default="task", nullable=False)
    planning_source: Mapped[str] = mapped_column(
        String(24), default="pydantic_ai", nullable=False
    )
    model_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Requests - 1: a retry means the model was asked again after validation
    # rejected its first output, so it is a signal, not an error rate.
    retries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="OK", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
