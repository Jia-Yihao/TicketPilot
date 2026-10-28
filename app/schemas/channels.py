from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Why a channel's inbound path is or is not usable. A closed set: the order of
# the members is the order the predicate evaluates them in, and it is the only
# declaration of these strings in the codebase. ``app/channels`` (tier 3)
# imports it from here (tier 1); nothing in ``app/schemas`` may import back.
ChannelAvailability = Literal[
    "available", "webhook_secret_missing", "not_enabled", "not_implemented"
]


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
    availability: ChannelAvailability
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
