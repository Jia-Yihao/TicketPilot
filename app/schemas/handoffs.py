from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HandoffSummary(BaseModel):
    event_id: str
    conversation_id: str
    signal_id: str
    reason: str
    priority: str
    queue: str
    status: str
    intent: str | None = None
    channel: str = "WEB"
    detail: str = ""
    claimed_by: str | None = None
    created_at: str
    updated_at: str
    transcript_digest: str = ""


class HandoffQueueResponse(BaseModel):
    items: list[HandoffSummary]
    total: int
    limit: int
    offset: int


class HandoffDetail(HandoffSummary):
    user_id: str
    trace_id: str | None = None
    pending_task_id: str | None = None
    claimed_at: str | None = None
    released_by: str | None = None
    closed_by: str | None = None
    resolution: str | None = None
    bundle: dict[str, Any] = Field(default_factory=dict)


class HandoffActionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)
    resolution: str | None = Field(default=None, max_length=64)


class HandoffMetricsResponse(BaseModel):
    window_minutes: int | None = None
    conversations: int
    handoff_conversations: int
    handoff_rate: float
    auto_resolution_rate: float
    open_cases: int
    by_queue: dict[str, int]
    by_reason: dict[str, int]
    by_signal: dict[str, int]
    by_hour: dict[str, int]
    abandon_events: int
    abandon_rate: float
    abandon_by_channel: dict[str, int]
    abandon_by_intent: dict[str, int]
    abandon_by_hour: dict[str, int]


class AbandonSummary(BaseModel):
    event_id: str
    conversation_id: str
    intent: str | None = None
    channel: str
    reason: str
    idle_minutes: int
    created_at: str


class AbandonListResponse(BaseModel):
    items: list[AbandonSummary]
    total: int
