from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ticket import Ticket


class TicketRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, ticket_id: str) -> Ticket | None:
        return self.session.get(Ticket, ticket_id)

    def get_by_idempotency(self, tenant_id: str, idempotency_key: str) -> Ticket | None:
        stmt = select(Ticket).where(
            Ticket.tenant_id == tenant_id,
            Ticket.idempotency_key == idempotency_key,
        )
        return self.session.scalars(stmt).first()

    def create(self, ticket: Ticket) -> Ticket:
        self.session.add(ticket)
        self.session.flush()
        return ticket
