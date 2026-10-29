from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from app.channels.base import (
    InboundRequest,
    MessageEnvelope,
    RenderedMessage,
    header,
    json_object,
    required_str,
)
from app.channels.render import capabilities_for, render_response
from app.core.errors import UnauthorizedError
from app.models.enums import Channel

__all__ = ["WebChannelAdapter"]


class WebChannelAdapter:
    """The existing console channel, behind the same seam as every other.

    Identity still comes from the dev header adapter, never from the JSON
    body. ``channel_message_id`` is optional: when the client sends one, a
    retried delivery is answered from the stored reply instead of running
    the turn twice.
    """

    channel = Channel.WEB
    capabilities = capabilities_for(Channel.WEB)

    def __init__(self, *, sla_hours: int = 24) -> None:
        self.sla_hours = sla_hours

    def verify(self, request: InboundRequest) -> None:
        if request.identity is None:
            raise UnauthorizedError("Web channel requires a resolved identity.")

    def normalize(self, request: InboundRequest) -> MessageEnvelope:
        if request.identity is None:
            raise UnauthorizedError("Web channel requires a resolved identity.")
        data = json_object(request.body)
        conversation_id = data.get("conversation_id")
        order_id = data.get("order_id")
        declared = data.get("intent")
        return MessageEnvelope(
            channel=self.channel,
            channel_message_id=self._message_id(request, data),
            conversation_id=(
                conversation_id if isinstance(conversation_id, str) else None
            ),
            identity=request.identity,
            text=required_str(data, "message"),
            metadata={
                "order_id": order_id if isinstance(order_id, str) else None,
            },
            # 与 RenderedMessage.intent 同形的判据：非 str 即 None，不强转（§4.2）。
            # 边界（T6-7）：``accept_web`` 重建 payload 时从不携带 intent，
            # 所以网页入口今天没有任何声明值能走到这里。
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

    def _message_id(self, request: InboundRequest, data: Mapping[str, Any]) -> str:
        provided = header(request, "x-channel-message-id")
        if provided:
            return provided[:160]
        body_value = data.get("channel_message_id")
        if isinstance(body_value, str) and body_value.strip():
            return body_value.strip()[:160]
        return f"web-{uuid4().hex}"
