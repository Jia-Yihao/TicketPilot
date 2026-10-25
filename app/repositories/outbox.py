from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from app.core.security import Identity
from app.core.time import utcnow
from app.models.enums import OutboxStatus
from app.models.outbox import OutboxMessage

__all__ = ["OutboxRepository", "is_staff"]


def is_staff(identity: Identity) -> bool:
    """A seat may read the whole tenant's queue; a customer reads only their own."""
    return identity.has_role("support") or identity.has_role("approver")


class OutboxRepository:
    """Reads and claims the queue."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(
        self,
        *,
        tenant_id: str,
        user_id: str,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        dedupe_key: str,
        payload: dict[str, Any] | None = None,
        available_at: datetime | None = None,
    ) -> tuple[OutboxMessage, bool]:
        """Write the message, unless the same fact is already queued.

        Returns the row and whether it was created. A duplicate is not an
        error: the same business fact may be enqueued twice, and it must
        still produce one message.
        """
        existing = self.session.execute(
            select(OutboxMessage).where(OutboxMessage.dedupe_key == dedupe_key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False

        row = OutboxMessage(
            id=f"obx_{uuid.uuid4().hex[:16]}",
            tenant_id=tenant_id,
            user_id=user_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            dedupe_key=dedupe_key,
            payload=payload or {},
            status=OutboxStatus.PENDING.value,
            retry_count=0,
            available_at=available_at or utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        return row, True

    def due(self, *, now: datetime | None = None, limit: int = 50) -> list[OutboxMessage]:
        """The messages a worker may pick up right now."""
        moment = now or utcnow()
        query = (
            select(OutboxMessage)
            .where(
                OutboxMessage.status == OutboxStatus.PENDING.value,
                OutboxMessage.available_at <= moment,
            )
            .order_by(OutboxMessage.available_at, OutboxMessage.id)
            .limit(limit)
        )
        return list(self.session.execute(query).scalars().all())

    def claim(self, row_id: str) -> bool:
        """Take ownership of a message.

        The update is conditional on the status, so two workers racing for
        the same row produce exactly one winner on every database.
        """
        stmt = (
            update(OutboxMessage)
            .where(
                OutboxMessage.id == row_id,
                OutboxMessage.status == OutboxStatus.PENDING.value,
            )
            .values(status=OutboxStatus.PROCESSING.value, updated_at=utcnow())
            .returning(OutboxMessage.id)
            .execution_options(synchronize_session=False)
        )
        return self.session.execute(stmt).scalar_one_or_none() is not None

    def mark_sent(self, row_id: str) -> None:
        self.session.execute(
            update(OutboxMessage)
            .where(OutboxMessage.id == row_id)
            .values(
                status=OutboxStatus.SENT.value,
                sent_at=utcnow(),
                last_error=None,
                updated_at=utcnow(),
            )
        )

    def mark_failed(
        self, row_id: str, *, error: str, retry_at: datetime, dead_letter: bool
    ) -> None:
        self.session.execute(
            update(OutboxMessage)
            .where(OutboxMessage.id == row_id)
            .values(
                status=(
                    OutboxStatus.FAILED.value
                    if dead_letter
                    else OutboxStatus.PENDING.value
                ),
                retry_count=OutboxMessage.retry_count + 1,
                available_at=retry_at,
                last_error=error[:500],
                updated_at=utcnow(),
            )
        )

    def cancel(self, row_id: str) -> None:
        self.session.execute(
            update(OutboxMessage)
            .where(
                OutboxMessage.id == row_id,
                OutboxMessage.status == OutboxStatus.PENDING.value,
            )
            .values(status=OutboxStatus.CANCELLED.value, updated_at=utcnow())
        )

    def _scoped(self, identity: Identity) -> Select[tuple[OutboxMessage]]:
        query = select(OutboxMessage).where(OutboxMessage.tenant_id == identity.tenant_id)
        if not is_staff(identity):
            query = query.where(OutboxMessage.user_id == identity.user_id)
        return query

    def list_for(
        self,
        identity: Identity,
        *,
        status: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OutboxMessage]:
        query = self._scoped(identity)
        if status:
            query = query.where(OutboxMessage.status == status.upper())
        if event_type:
            query = query.where(OutboxMessage.event_type == event_type)
        query = (
            query.order_by(OutboxMessage.created_at.desc(), OutboxMessage.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.execute(query).scalars().all())

    def count_for(self, identity: Identity, *, status: str | None = None) -> int:
        query = self._scoped(identity).with_only_columns(func.count(OutboxMessage.id))
        if status:
            query = query.where(OutboxMessage.status == status.upper())
        return int(self.session.execute(query).scalar_one())

    def get(self, row_id: str) -> OutboxMessage | None:
        return self.session.get(OutboxMessage, row_id)
