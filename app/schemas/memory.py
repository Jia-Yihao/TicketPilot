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


class ProactiveRuleListResponse(BaseModel):
    rules: list[ProactiveRuleView] = Field(default_factory=list)
