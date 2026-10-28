from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base
from app.models.enums import TaskStatus


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        # A client retry with the same key must find the first task, not a
        # second one. The key is scoped to its owner: two customers may use
        # the same opaque string without colliding.
        UniqueConstraint(
            "tenant_id", "user_id", "idempotency_key", name="uq_tasks_idempotency"
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, native_enum=False, length=32, validate_strings=True),
        default=TaskStatus.CREATED,
        index=True,
    )
    request: Mapped[str] = mapped_column(Text)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    # R9 关联键：对话树与任务树落到同一个对话上。只走列——写进 context 会改
    # request_digest，把同一幂等键的重试从「返回第一个任务」变成 409。
    # 无外键：与 tasks.trace_id / trace_spans.conversation_id 同形状（裸列 + 索引）。
    conversation_id: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )
    # The replay contract: same key + same digest returns the first task;
    # same key + a different request is a 409, never a silent overwrite.
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    request_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (Index("ix_task_events_task_created", "task_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
