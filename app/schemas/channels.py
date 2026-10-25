from __future__ import annotations

from pydantic import BaseModel, Field


class ChannelReply(BaseModel):
    channel: str
    channel_message_id: str
    conversation_id: str | None = None
    text: str
    subject: str | None = None
    truncated: bool = False
    deduplicated: bool = False
    context_references: list[str] = Field(default_factory=list)
    intent: str | None = None
    task_id: str | None = None


class ChannelCapabilityView(BaseModel):
    channel: str
    enabled: bool
    supports_stream: bool
    supports_rich_card: bool
    supports_voice: bool
    max_length: int | None
    max_cards: int
    sentence_char_limit: int | None
    formal: bool
    offers_human_handoff: bool
    deliveries: dict[str, int] = Field(default_factory=dict)


class ChannelMatrixResponse(BaseModel):
    channels: list[ChannelCapabilityView]
