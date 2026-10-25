from __future__ import annotations

from pydantic import BaseModel


class OutboxMessageSummary(BaseModel):
    id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    status: str
    retry_count: int
    last_error: str | None = None
    created_at: str
    updated_at: str
    sent_at: str | None = None


class OutboxQueueResponse(BaseModel):
    items: list[OutboxMessageSummary]
    total: int
    limit: int
    offset: int


class OutboxDeliveryResponse(BaseModel):
    claimed: int
    sent: int
    failed: int
    dead_lettered: int
