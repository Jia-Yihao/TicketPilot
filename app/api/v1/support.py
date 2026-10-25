from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from app.agent.support_agent import SupportAgent
from app.api.deps import get_channel_gateway, get_support_agent
from app.channels.gateway import ChannelGateway
from app.core.security import Identity, get_identity
from app.schemas.support import (
    ContextCard,
    ContextSectionDetail,
    SupportMessageRequest,
    SupportMessageResponse,
)

router = APIRouter(prefix="/api/v1/support", tags=["support"])


@router.post("/messages", response_model=SupportMessageResponse)
def send_message(
    body: SupportMessageRequest,
    request: Request,
    http_response: Response,
    identity: Identity = Depends(get_identity),
    gateway: ChannelGateway = Depends(get_channel_gateway),
) -> dict[str, Any]:
    """The legacy entry, now the same code path as the web channel.

    Without ``X-Channel-Message-Id`` nothing is recorded and every call runs a
    turn, as before; with the header a retry reads back the stored reply.
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


@router.get("/cards/{kind}/{card_id}", response_model=ContextCard)
def get_card(
    kind: str,
    card_id: str,
    identity: Identity = Depends(get_identity),
    agent: SupportAgent = Depends(get_support_agent),
) -> dict[str, Any]:
    return agent.get_card(identity, kind, card_id)


@router.get(
    "/cards/{kind}/{card_id}/sections/{section_id}",
    response_model=ContextSectionDetail,
)
def get_card_section(
    kind: str,
    card_id: str,
    section_id: str,
    identity: Identity = Depends(get_identity),
    agent: SupportAgent = Depends(get_support_agent),
) -> dict[str, Any]:
    return agent.get_section(identity, kind, card_id, section_id)
