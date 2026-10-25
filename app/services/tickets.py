from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.security import Identity
from app.models.enums import TicketStatus
from app.models.ticket import Ticket
from app.repositories.audit import AuditRepository
from app.repositories.tickets import TicketRepository

CATEGORIES = {"ORDER", "LOGISTICS", "REFUND", "OTHER"}
PRIORITIES = {"LOW", "NORMAL", "HIGH", "URGENT"}


@dataclass(frozen=True, slots=True)
class TicketCreation:
    ticket: Ticket
    created: bool


class TicketService:
    def __init__(self, session: Session) -> None:
        self.repo = TicketRepository(session)
        self.audit = AuditRepository(session)

    def create(
        self,
        identity: Identity,
        *,
        category: str,
        priority: str,
        subject: str,
        description: str,
        idempotency_key: str,
    ) -> TicketCreation:
        category = category.upper()
        priority = priority.upper()
        if category not in CATEGORIES:
            raise ValidationAppError(f"Unsupported ticket category: {category}.")
        if priority not in PRIORITIES:
            raise ValidationAppError(f"Unsupported ticket priority: {priority}.")
        if not subject.strip() or not description.strip():
            raise ValidationAppError("Ticket subject and description are required.")
        if not idempotency_key.strip():
            raise ValidationAppError("Idempotency-Key is required for ticket creation.")

        existing = self.repo.get_by_idempotency(identity.tenant_id, idempotency_key)
        if existing is not None:
            if existing.user_id != identity.user_id:
                raise ConflictError("Idempotency key belongs to another user.")
            return TicketCreation(ticket=existing, created=False)

        token = uuid.uuid4().hex[:10].upper()
        ticket = Ticket(
            id=f"tkt_{token.lower()}",
            ticket_no=f"TCK-{token}",
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            category=category,
            priority=priority,
            subject=subject.strip(),
            description=description.strip(),
            status=TicketStatus.OPEN,
            idempotency_key=idempotency_key,
        )
        self.repo.create(ticket)
        self.audit.record(
            actor_id=identity.user_id,
            tenant_id=identity.tenant_id,
            action="ticket.create",
            resource_type="ticket",
            resource_id=ticket.id,
            outcome="SUCCESS",
            detail={"category": category, "priority": priority},
        )
        return TicketCreation(ticket=ticket, created=True)

    def get(self, identity: Identity, ticket_id: str) -> Ticket:
        ticket = self.repo.get(ticket_id)
        if ticket is None or ticket.tenant_id != identity.tenant_id:
            raise NotFoundError("Ticket not found.")
        if ticket.user_id != identity.user_id and not identity.has_role("support"):
            raise NotFoundError("Ticket not found.")
        return ticket
