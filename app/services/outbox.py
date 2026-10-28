"""The outbox: a message that is written with the fact, and sent later.

Two things can go wrong when a business fact has to reach the outside
world. The write may succeed and the push may fail, or the push may
succeed and the write may not. The outbox removes the second case: the
message row is written in the same transaction as the fact, so the two
either both happen or neither does.

The delivery half is a worker loop. It claims a row, hands it to a sink
and marks what happened. A sink that raises has not delivered: the row
goes back to pending with a backoff, and after the last attempt it
becomes the dead letter, where an operator can find it.

The sink is injected. This project has no external system to push to, and
a queue that pretends to have one would be a queue that lies.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.security import Identity
from app.core.time import utcnow
from app.models.outbox import OutboxMessage
from app.repositories.outbox import OutboxRepository

__all__ = [
    "DeliveryReport",
    "MessageNotDelivered",
    "OutboxService",
    "OutboxSink",
]

OutboxSink = Callable[[OutboxMessage], None]


class MessageNotDelivered(Exception):
    """The message has not arrived yet and should be tried again later."""


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    """What one pass of the worker did."""

    claimed: int = 0
    sent: int = 0
    failed: int = 0
    dead_lettered: int = 0


class OutboxService:
    """Enqueues messages in the caller's transaction and delivers them later."""

    def __init__(
        self,
        session: Session,
        *,
        max_attempts: int = 5,
        backoff_seconds: int = 30,
        max_backoff_seconds: int = 3600,
    ) -> None:
        self.session = session
        self.repo = OutboxRepository(session)
        self.max_attempts = max(1, max_attempts)
        self.backoff_s = max(1, backoff_seconds)
        self.max_backoff_s = max(self.backoff_s, max_backoff_seconds)

    def enqueue(
        self,
        *,
        identity: Identity,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        dedupe_key: str,
        payload: dict[str, Any] | None = None,
        delay_seconds: int = 0,
    ) -> OutboxMessage:
        """Queue a message. The caller decides when the transaction commits.

        Enqueuing the same ``dedupe_key`` twice keeps the first row, so a
        retry of the business write cannot produce a second notification.
        """
        available_at = utcnow() + timedelta(seconds=max(0, delay_seconds))
        row, _created = self.repo.enqueue(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            dedupe_key=dedupe_key,
            payload=payload or {},
            available_at=available_at,
        )
        return row

    def deliver(self, sink: OutboxSink, *, limit: int = 50) -> DeliveryReport:
        """Push the due messages and record what happened to each one.

        The claim is committed before the push, so a worker that dies
        mid-push leaves the row claimed rather than pretending it was
        sent. A sink that raises means the message did not arrive: the
        row goes back to pending with a backoff, and once the attempts
        run out the row becomes the dead letter.
        """
        claimed = 0
        sent = 0
        failed = 0
        dead = 0

        for row in self.repo.due(limit=limit):
            if not self.repo.claim(row.id):
                continue
            self.session.commit()
            claimed += 1
            attempts = row.retry_count + 1
            try:
                sink(row)
            except Exception as exc:  # noqa: BLE001 - the failure is recorded
                self.session.rollback()
                dead_letter = attempts >= self.max_attempts
                self.repo.mark_failed(
                    row.id,
                    error=f"{type(exc).__name__}: {exc}",
                    retry_at=utcnow() + timedelta(seconds=self._backoff(attempts)),
                    dead_letter=dead_letter,
                )
                self.session.commit()
                if dead_letter:
                    dead += 1
                else:
                    failed += 1
                continue
            self.repo.mark_sent(row.id)
            self.session.commit()
            sent += 1

        return DeliveryReport(
            claimed=claimed, sent=sent, failed=failed, dead_lettered=dead
        )

    def _backoff(self, attempts: int) -> int:
        shift = max(0, attempts - 1)
        return min(self.max_backoff_s, self.backoff_s * (1 << shift))
