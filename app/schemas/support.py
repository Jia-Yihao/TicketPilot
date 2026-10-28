from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.tasks import TaskResponse


class SupportMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=80)
    order_id: str | None = Field(default=None, max_length=80)


class ContextSectionSummary(BaseModel):
    id: str
    title: str
    preview: str
    chars: int


class ContextCard(BaseModel):
    kind: str
    card_id: str
    title: str
    subtitle: str | None = None
    summary: str
    badges: list[str] = Field(default_factory=list)
    fields: dict[str, str | int | bool | None] = Field(default_factory=dict)
    sections: list[ContextSectionSummary] = Field(default_factory=list)
    source: str | None = None
    total_chars: int = 0


class ContextSectionContent(BaseModel):
    id: str
    title: str
    content: str
    chars: int


class ContextSectionDetail(BaseModel):
    kind: str
    card_id: str
    title: str
    section: ContextSectionContent
    source: str | None = None
    citation: dict[str, Any] | None = None


class SupportClarification(BaseModel):
    reason: str
    prompt: str
    # 反问轮缺的是意图不是槽位，续跑也不携带未确认的读数
    # （``.qoder/rules/clarify-counter-question.md`` §6.1），两者都会以 null 出现。
    expected_slot: str | None
    resumable_intent: str | None


class HandoffTicket(BaseModel):
    """What the customer is told about their case."""

    event_id: str
    status: str
    queue: str
    priority: str
    reason: str
    signal_id: str
    takeover: bool


class SupportPersonalization(BaseModel):
    """Which profile fields reshaped this reply — names, never values.

    ``effects`` are the codes from ``app/agent/personalization.py``. Nothing
    the customer wrote travels in here, so the response body stays safe to
    log and to hand to a seat.
    """

    effects: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)


class SupportMessageResponse(BaseModel):
    conversation_id: str
    reply: str
    intent: str
    agent_mode: str
    context_cards: list[ContextCard] = Field(default_factory=list)
    task: TaskResponse | None = None
    suggestions: list[str] = Field(default_factory=list)
    slots: dict[str, Any] = Field(default_factory=dict)
    inherited_slots: list[str] = Field(default_factory=list)
    clarification: SupportClarification | None = None
    trace_id: str | None = None
    agent_silent: bool = False
    handoff: HandoffTicket | None = None
    personalization: SupportPersonalization | None = None
