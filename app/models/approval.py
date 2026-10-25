from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base
from app.models.enums import ApprovalStatus


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        Index(
            "uq_approvals_one_pending_per_task",
            "task_id",
            unique=True,
            sqlite_where=text("status = 'PENDING'"),
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index(
            "uq_approvals_open_per_resource",
            "resource_type",
            "resource_id",
            "action_type",
            unique=True,
            sqlite_where=text("status IN ('PENDING', 'APPROVED')"),
            postgresql_where=text("status IN ('PENDING', 'APPROVED')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    action_type: Mapped[str] = mapped_column(String(32))
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[str] = mapped_column(String(64))
    action_digest: Mapped[str] = mapped_column(String(64))
    resource_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[ApprovalStatus] = mapped_column(
        SAEnum(ApprovalStatus, native_enum=False, length=32, validate_strings=True),
        default=ApprovalStatus.PENDING,
        index=True,
    )
    requested_by: Mapped[str] = mapped_column(String(64))
    reviewer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    action_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
