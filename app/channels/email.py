from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import SecretStr

from app.channels.base import (
    Attachment,
    InboundRequest,
    MessageEnvelope,
    RenderedMessage,
    json_object,
    required_str,
    resolve_identity,
    verify_hmac,
)
from app.channels.render import capabilities_for, render_response
from app.core.errors import ValidationAppError
from app.models.enums import Channel

__all__ = ["EmailChannelAdapter"]

_MAX_ATTACHMENTS = 5


class EmailChannelAdapter:
    """Inbound mail webhook plus a formal outbound reply.

    The sender maps to a customer through configuration; the payload cannot
    name its own tenant. Replies always carry a reference, an SLA line and a
    signature, and remain readable as plain text.
    """

    channel = Channel.EMAIL
    capabilities = capabilities_for(Channel.EMAIL)

    def __init__(
        self,
        *,
        secret: SecretStr | None,
        identity_map: Mapping[str, str],
        max_skew_seconds: int,
        sla_hours: int,
    ) -> None:
        self.secret = secret
        self.identity_map = identity_map
        self.max_skew_seconds = max_skew_seconds
        self.sla_hours = sla_hours

    def verify(self, request: InboundRequest) -> None:
        verify_hmac(request, self.secret, max_skew_seconds=self.max_skew_seconds)

    def normalize(self, request: InboundRequest) -> MessageEnvelope:
        data = json_object(request.body)
        message_id = required_str(data, "message_id", max_length=160)
        sender = required_str(data, "from", max_length=320).lower()
        identity = resolve_identity(self.identity_map, sender, channel=self.channel)
        text = self._text(data)
        conversation_id = data.get("conversation_id")
        locale = data.get("locale")
        declared = data.get("intent")
        return MessageEnvelope(
            channel=self.channel,
            channel_message_id=message_id,
            conversation_id=(
                conversation_id if isinstance(conversation_id, str) else None
            ),
            identity=identity,
            text=text,
            attachments=self._attachments(data),
            locale=locale if isinstance(locale, str) and locale else "zh-CN",
            # 与 RenderedMessage.intent 同形的判据：非 str 即 None，不强转（§4.2）。
            declared_intent=declared if isinstance(declared, str) else None,
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

    def _text(self, data: Mapping[str, Any]) -> str:
        for key in ("text", "body", "subject"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:2000]
        raise ValidationAppError("Email webhook has no readable text body.")

    def _attachments(self, data: Mapping[str, Any]) -> tuple[Attachment, ...]:
        raw = data.get("attachments")
        if not isinstance(raw, list):
            return ()
        parsed: list[Attachment] = []
        for item in raw[:_MAX_ATTACHMENTS]:
            if not isinstance(item, Mapping):
                continue
            attachment_id = item.get("attachment_id") or item.get("id")
            filename = item.get("filename")
            content_type = item.get("content_type")
            size_bytes = item.get("size_bytes")
            if not isinstance(attachment_id, str) or not isinstance(filename, str):
                continue
            parsed.append(
                Attachment(
                    attachment_id=attachment_id[:80],
                    filename=filename[:200],
                    content_type=(
                        content_type
                        if isinstance(content_type, str)
                        else "application/octet-stream"
                    ),
                    size_bytes=size_bytes if isinstance(size_bytes, int) else 0,
                )
            )
        return tuple(parsed)
