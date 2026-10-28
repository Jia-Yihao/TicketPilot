from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base


class CustomerProfile(Base):
    """What the customer chose to tell us, and nothing the agent guessed.

    Only allowlisted preference keys and a user-written history summary are
    stored. There is no phone, address, payment or identity column, and the
    write path rejects or masks values that look like them.
    """

    __tablename__ = "customer_profiles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_customer_profiles_owner"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    preferred_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    locale: Mapped[str] = mapped_column(String(16), default="zh-CN", nullable=False)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    history_summary: Mapped[str] = mapped_column(Text, default="")
    proactive_opt_in: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    muted_event_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    max_notifications_per_day: Mapped[int] = mapped_column(
        Integer, default=3, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class ProactiveRule(Base):
    """One tenant's answer to: when this event happens, say what?

    The rule carries the template and the limits. The service may suppress a
    rule outcome, but it may not invent a message or a trigger of its own.
    """

    __tablename__ = "proactive_rules"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "event_type", name="uq_proactive_rules_tenant_event"
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    template: Mapped[str] = mapped_column(Text)
    requires_confirmation: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    # Which approved state is live. Only ``approve_change`` moves it forward;
    # every move writes one immutable ``ProactiveRuleVersion`` row.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class ProactiveRuleVersion(Base):
    """An immutable snapshot of one tenant's rule after an approved change.

    Rows are only inserted. Nothing in the service updates or deletes them, so
    a notification can name the exact words it used and an operator can diff
    two versions or roll back to one without trusting an audit digest alone.
    """

    __tablename__ = "proactive_rule_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "event_type",
            "version",
            name="uq_proactive_rule_versions_number",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    template: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    change_id: Mapped[str] = mapped_column(String(40), index=True)
    submitted_by: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )


class ProactiveRuleChange(Base):
    """A proposed rule state waiting for a second pair of eyes.

    ``pending_key`` equals the event type while the change is pending and is
    cleared on any decision; the unique constraint on ``(tenant_id,
    pending_key)`` is what makes two concurrent submissions collide instead of
    both looking valid to a read-then-write check.
    """

    __tablename__ = "proactive_rule_changes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "pending_key",
            name="uq_proactive_rule_changes_pending",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)
    pending_key: Mapped[str | None] = mapped_column(String(48), nullable=True)
    base_version: Mapped[int] = mapped_column(Integer)
    target_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean)
    cooldown_minutes: Mapped[int] = mapped_column(Integer)
    old_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    old_cooldown_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    submitted_by: Mapped[str] = mapped_column(String(64))
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    applied_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Notification(Base):
    """A proactive proposal waiting for the customer's yes or no.

    Confirmation is the action: it writes an outbox row in the same
    transaction, so ``CONFIRMED`` cannot exist without a queued delivery.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "event_type",
            "event_key",
            name="uq_notifications_event_key",
        ),
        Index("ix_notifications_user_created", "tenant_id", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    event_key: Mapped[str] = mapped_column(String(120))
    rule_id: Mapped[str] = mapped_column(String(40), index=True)
    # Which approved rule snapshot produced this body. The text stays as it
    # was rendered; a later change or rollback never rewrites it.
    rule_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(24), index=True)
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
