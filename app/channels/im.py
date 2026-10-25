from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import SecretStr

from app.channels.base import (
    InboundRequest,
    MessageEnvelope,
    RenderedMessage,
    json_object,
    resolve_identity,
    verify_hmac,
)
from app.channels.render import capabilities_for, render_response
from app.core.errors import ValidationAppError
from app.models.enums import Channel

__all__ = ["ImChannelAdapter"]


class ImChannelAdapter:
    """WeCom / WeChat callback adapter.

    Text is capped at the channel's 200 characters at a sentence boundary.
    Context cards are downgraded to a numbered "reply for details" index
    instead of being dropped silently.
    """

    def __init__(
        self,
        *,
        channel: Channel,
        secret: SecretStr | None,
        identity_map: Mapping[str, str],
        max_skew_seconds: int,
        sla_hours: int,
    ) -> None:
        if channel not in (Channel.WECHAT, Channel.WECOM):
            raise ValueError("ImChannelAdapter only serves WECHAT and WECOM.")
        self.channel: Channel = channel
        self.capabilities = capabilities_for(channel)
        self.secret = secret
        self.identity_map = identity_map
        self.max_skew_seconds = max_skew_seconds
        self.sla_hours = sla_hours

    def verify(self, request: InboundRequest) -> None:
        verify_hmac(request, self.secret, max_skew_seconds=self.max_skew_seconds)

    def normalize(self, request: InboundRequest) -> MessageEnvelope:
        data = json_object(request.body)
        message_id = self._first(data, ("msg_id", "message_id", "MsgId"), 160)
        sender = self._first(data, ("from_user", "openid", "FromUserName"), 160)
        identity = resolve_identity(self.identity_map, sender, channel=self.channel)
        text = self._first(data, ("text", "content", "Content"), 2000)
        conversation_id = data.get("conversation_id")
        return MessageEnvelope(
            channel=self.channel,
            channel_message_id=message_id,
            conversation_id=(
                conversation_id if isinstance(conversation_id, str) else None
            ),
            identity=identity,
            text=text,
        )

    def render(
        self,
        response: Mapping[str, Any],
        *,
        deduplicated: bool = False,
    ) -> RenderedMessage:
        return render_response(
            self.channel,
            response,
            sla_hours=self.sla_hours,
            deduplicated=deduplicated,
        )

    def _first(
        self,
        data: Mapping[str, Any],
        keys: tuple[str, ...],
        max_length: int,
    ) -> str:
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                if len(value) > max_length:
                    raise ValidationAppError(
                        f"Webhook field '{key}' exceeds {max_length} characters."
                    )
                return value.strip()
        raise ValidationAppError(f"Webhook field '{keys[0]}' is required.")
