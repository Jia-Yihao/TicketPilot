from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import get_channel_gateway
from app.channels.gateway import ChannelGateway
from app.core.security import Identity, get_identity
from app.schemas.channels import ChannelMatrixResponse, ChannelReply
from app.schemas.support import SupportMessageRequest, SupportMessageResponse

router = APIRouter(prefix="/api/v1/channels", tags=["channels"])


@router.get("", response_model=ChannelMatrixResponse)
def channel_matrix(
    identity: Identity = Depends(get_identity),
    gateway: ChannelGateway = Depends(get_channel_gateway),
) -> dict[str, Any]:
    """列出各渠道的可用状态；只读，与租户无关。"""
    del identity
    return {"channels": gateway.matrix()}


@router.post("/web/messages", response_model=SupportMessageResponse)
def web_message(
    body: SupportMessageRequest,
    request: Request,
    http_response: Response,
    identity: Identity = Depends(get_identity),
    gateway: ChannelGateway = Depends(get_channel_gateway),
) -> dict[str, Any]:
    """The console channel, unchanged apart from optional delivery dedup.

    Sending ``X-Channel-Message-Id`` makes a retry read the stored reply.
    Without the header the route is a transparent wrapper over the agent.
    """
    payload, deduplicated = gateway.accept_web(
        identity,
        body.message,
        conversation_id=body.conversation_id,
        order_id=body.order_id,
        channel_message_id=request.headers.get("x-channel-message-id"),
    )
    if deduplicated:
        http_response.headers["X-Channel-Deduplicated"] = "true"
    return payload


@router.post("/{channel}/webhook", response_model=ChannelReply)
async def channel_webhook(
    channel: str,
    request: Request,
    gateway: ChannelGateway = Depends(get_channel_gateway),
) -> dict[str, Any]:
    """Signed inbound delivery for email and IM providers.

    The raw body is verified before parsing. Identity comes from the
    channel's configured sender map; nothing in the body can select a
    tenant.
    """
    body = await request.body()
    rendered = gateway.accept_webhook(channel, dict(request.headers), body)
    return {
        "channel": channel.upper(),
        "channel_message_id": rendered.channel_message_id,
        "conversation_id": rendered.conversation_id,
        "text": rendered.text,
        "subject": rendered.subject,
        "truncated": rendered.truncated,
        "deduplicated": rendered.deduplicated,
        "context_references": list(rendered.context_references),
        "intent": rendered.intent,
        "task_id": rendered.task_id(),
    }
