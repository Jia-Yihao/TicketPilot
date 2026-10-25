from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.core.security import Identity, get_identity
from app.schemas.tickets import TicketCreateRequest, TicketResponse
from app.services.tickets import TicketService

router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])


@router.post("", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def create_ticket(
    body: TicketCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    creation = TicketService(session).create(
        identity,
        category=body.category,
        priority=body.priority,
        subject=body.subject,
        description=body.description,
        idempotency_key=idempotency_key,
    )
    session.commit()
    ticket = creation.ticket
    return {
        "ticket_id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "status": ticket.status.value,
        "category": ticket.category,
        "priority": ticket.priority,
        "subject": ticket.subject,
        "created_at": ticket.created_at.isoformat(),
    }


@router.get("/{ticket_id}", response_model=TicketResponse)
def get_ticket(
    ticket_id: str,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ticket = TicketService(session).get(identity, ticket_id)
    return {
        "ticket_id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "status": ticket.status.value,
        "category": ticket.category,
        "priority": ticket.priority,
        "subject": ticket.subject,
        "created_at": ticket.created_at.isoformat(),
    }
