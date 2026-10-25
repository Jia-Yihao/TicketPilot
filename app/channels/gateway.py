from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.agent.support_agent import SupportAgent
from app.channels.base import ChannelAdapter, InboundRequest, RenderedMessage
from app.channels.registry import build_adapters
from app.channels.render import capabilities_for
from app.channels.store import ChannelMessageStore
from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationAppError
from app.core.security import Identity
from app.models.enums import Channel

__all__ = ["ChannelGateway"]

_WEBHOOK_CHANNELS = frozenset({Channel.EMAIL, Channel.WECHAT, Channel.WECOM})


class ChannelGateway:
    """The only place a transport request becomes an agent turn.

    Verification and identity resolution happen in the adapter; delivery
    idempotency happens here; the agent receives the same call it always
    did. Adding a channel therefore adds no branch to ``app/agent``.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        agent: SupportAgent,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.adapters = build_adapters(settings)
        self.store = ChannelMessageStore(
            session_factory,
            claim_timeout_seconds=settings.channel_delivery_claim_timeout_seconds,
        )

    def adapter(self, channel: Channel) -> ChannelAdapter:
        adapter = self.adapters.get(channel)
        if adapter is None:
            raise NotFoundError(f"Channel {channel.value} is not enabled.")
        return adapter

    def accept_web(
        self,
        identity: Identity,
        message: str,
        *,
        conversation_id: str | None,
        order_id: str | None,
        channel_message_id: str | None,
    ) -> tuple[dict[str, Any], bool]:
        if channel_message_id is None:
            return (
                self.agent.handle(
                    identity,
                    message,
                    conversation_id=conversation_id,
                    order_id=order_id,
                    channel=Channel.WEB,
                ),
                False,
            )

        adapter = self.adapter(Channel.WEB)
        payload = {
            "message": message,
            "conversation_id": conversation_id,
            "order_id": order_id,
        }
        request = InboundRequest(
            headers={"x-channel-message-id": channel_message_id},
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            identity=identity,
        )
        adapter.verify(request)
        envelope = adapter.normalize(request)
        stored, created = self.store.claim(envelope)
        if not created:
            return stored.response_payload or {}, True

        try:
            response = self.agent.handle(
                identity,
                message,
                conversation_id=conversation_id,
                order_id=order_id,
                channel=Channel.WEB,
            )
        except Exception:
            self.store.fail(
                channel=envelope.channel.value,
                channel_message_id=envelope.channel_message_id,
                error_code="AGENT_ERROR",
            )
            raise
        rendered = adapter.render(response)
        self.store.complete(
            channel=envelope.channel.value,
            channel_message_id=envelope.channel_message_id,
            payload=response,
            rendered_text=rendered.text,
        )
        return response, False

    def accept_webhook(
        self,
        channel_name: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> RenderedMessage:
        try:
            channel = Channel(channel_name.upper())
        except ValueError as exc:
            raise NotFoundError("Unknown channel.") from exc
        if channel not in _WEBHOOK_CHANNELS:
            raise NotFoundError("This channel has no inbound webhook.")
        adapter = self.adapter(channel)

        request = InboundRequest(headers=headers, body=body)
        adapter.verify(request)
        envelope = adapter.normalize(request)
        text = envelope.text or ""
        if not text.strip():
            if envelope.attachments:
                raise ValidationAppError(
                    "Attachments are not processed yet; send a text description."
                )
            raise ValidationAppError("The message has no text.")

        stored, created = self.store.claim(envelope)
        if not created:
            return replace(
                adapter.render(stored.response_payload or {}, deduplicated=True),
                channel_message_id=envelope.channel_message_id,
            )

        try:
            response = self.agent.handle(
                envelope.identity,
                text,
                conversation_id=envelope.conversation_id,
                channel=envelope.channel,
            )
        except Exception:
            self.store.fail(
                channel=envelope.channel.value,
                channel_message_id=envelope.channel_message_id,
                error_code="AGENT_ERROR",
            )
            raise
        rendered = adapter.render(response)
        self.store.complete(
            channel=envelope.channel.value,
            channel_message_id=envelope.channel_message_id,
            payload=response,
            rendered_text=rendered.text,
        )
        return replace(
            rendered, channel_message_id=envelope.channel_message_id
        )

    def matrix(self) -> list[dict[str, Any]]:
        counts = self.store.counts()
        items: list[dict[str, Any]] = []
        for channel in Channel:
            capabilities = capabilities_for(channel)
            items.append(
                {
                    "channel": channel.value,
                    "enabled": channel in self.adapters,
                    "supports_stream": capabilities.supports_stream,
                    "supports_rich_card": capabilities.supports_rich_card,
                    "supports_voice": capabilities.supports_voice,
                    "max_length": capabilities.max_length,
                    "max_cards": capabilities.max_cards,
                    "sentence_char_limit": capabilities.sentence_char_limit,
                    "formal": capabilities.formal,
                    "offers_human_handoff": capabilities.offers_human_handoff,
                    "deliveries": counts.get(channel.value, {}),
                }
            )
        return items
