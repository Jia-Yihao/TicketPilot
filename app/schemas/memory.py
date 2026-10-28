from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MemoryProfileResponse(BaseModel):
    preferred_name: str | None = None
    locale: str = "zh-CN"
    preferences: dict[str, Any] = Field(default_factory=dict)
    history_summary: str = ""
    proactive_opt_in: bool = False
    muted_event_types: list[str] = Field(default_factory=list)
    max_notifications_per_day: int = 3
    updated_at: str | None = None


class MemoryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_name: str | None = None
    locale: str | None = None
    preferences: dict[str, Any] | None = None
    history_summary: str | None = None
    proactive_opt_in: bool | None = None
    muted_event_types: list[str] | None = None
    max_notifications_per_day: int | None = None


class MemoryExportResponse(BaseModel):
    profile: MemoryProfileResponse
    notification_counts: dict[str, int] = Field(default_factory=dict)
    redacted: bool = True


class NotificationView(BaseModel):
    notification_id: str
    event_type: str
    status: str
    title: str
    body: str
    rule_version: int = 1
    created_at: str | None = None
    confirmed_at: str | None = None
    cancelled_at: str | None = None


class NotificationListResponse(BaseModel):
    notifications: list[NotificationView] = Field(default_factory=list)


class ProactiveEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=48)
    event_key: str = Field(min_length=1, max_length=120)
    context: dict[str, Any] | None = None


class ProactiveEventResponse(BaseModel):
    status: str
    reason: str | None = None
    notification: NotificationView | None = None


class ProactiveRuleView(BaseModel):
    rule_id: str
    event_type: str
    enabled: bool
    requires_confirmation: bool
    cooldown_minutes: int
    template: str
    required_context: list[str] = Field(default_factory=list)
    version: int = 1
    updated_at: str | None = None


class ProactiveRuleUpdateRequest(BaseModel):
    """The three fields a seat may change; ``requires_confirmation`` is not one.

    That flag is how ADR-014 keeps its promise — a notification waits for the
    customer before anything is queued. Making it a checkbox on this request
    would let the operations side remove the consent gate, so the field is
    rejected here rather than silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    template: str | None = None
    enabled: bool | None = None
    cooldown_minutes: int | None = Field(default=None, ge=0, le=1440)
    reason: str | None = Field(default=None, max_length=200)


class ProactiveRuleListResponse(BaseModel):
    rules: list[ProactiveRuleView] = Field(default_factory=list)


class ProactiveRuleChangeView(BaseModel):
    change_id: str
    event_type: str
    kind: str
    status: str
    base_version: int
    target_version: int | None = None
    template: str
    enabled: bool
    requires_confirmation: bool
    cooldown_minutes: int
    required_context: list[str] = Field(default_factory=list)
    old_template: str | None = None
    old_enabled: bool | None = None
    old_cooldown_minutes: int | None = None
    reason: str = ""
    submitted_by: str
    submitted_at: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    decision_note: str = ""
    applied_version: int | None = None


class ProactiveRuleChangeListResponse(BaseModel):
    changes: list[ProactiveRuleChangeView] = Field(default_factory=list)


class ProactiveRuleVersionView(BaseModel):
    event_type: str
    version: int
    template: str
    enabled: bool
    requires_confirmation: bool
    cooldown_minutes: int
    change_id: str
    submitted_by: str
    approved_by: str
    reason: str = ""
    created_at: str | None = None


class ProactiveRuleVersionListResponse(BaseModel):
    versions: list[ProactiveRuleVersionView] = Field(default_factory=list)


class ProactiveRuleDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=200)


class ProactiveRuleRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=200)
