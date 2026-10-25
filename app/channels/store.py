from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.channels.base import MessageEnvelope, envelope_digest
from app.core.errors import ConflictError
from app.core.time import utcnow
from app.models.channel_message import ChannelMessage
from app.models.enums import ChannelMessageStatus

__all__ = ["ChannelMessageStore", "StoredDelivery"]


@dataclass(frozen=True, slots=True)
class StoredDelivery:
    channel: str
    channel_message_id: str
    status: str
    response_payload: dict[str, Any] | None
    rendered_text: str | None


class ChannelMessageStore:
    """Claim, complete or fail one inbound delivery.

    The claim is a database insert, so two workers cannot both process the
    same provider message. A claim older than the timeout is allowed to be
    taken over: a crashed worker must not wedge the delivery forever.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        claim_timeout_seconds: int = 120,
    ) -> None:
        self.session_factory = session_factory
        self.claim_timeout_seconds = claim_timeout_seconds

    def claim(self, envelope: MessageEnvelope) -> tuple[StoredDelivery, bool]:
        digest = envelope_digest(envelope)
        with self.session_factory() as session:
            row = self._find(session, envelope)
            if row is not None:
                return self._claim_existing(session, row, digest)
            row = ChannelMessage(
                id=f"chm_{uuid.uuid4().hex[:16]}",
                channel=envelope.channel.value,
                channel_message_id=envelope.channel_message_id,
                tenant_id=envelope.identity.tenant_id,
                user_id=envelope.identity.user_id,
                conversation_id=envelope.conversation_id,
                status=ChannelMessageStatus.PROCESSING.value,
                request_digest=digest,
                attempts=1,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raced = self._find(session, envelope)
                if raced is None:
                    raise ConflictError(
                        "Could not claim the channel delivery.",
                        retryable=True,
                    ) from None
                return self._claim_existing(session, raced, digest)
            return self._snapshot(row), True

    def complete(
        self,
        *,
        channel: str,
        channel_message_id: str,
        payload: dict[str, Any],
        rendered_text: str,
    ) -> None:
        with self.session_factory() as session:
            row = self._find_by_key(session, channel, channel_message_id)
            if row is None:
                return
            row.status = ChannelMessageStatus.COMPLETED.value
            row.response_payload = dict(payload)
            row.rendered_text = rendered_text
            row.last_error = None
            row.updated_at = utcnow()
            session.commit()

    def fail(
        self,
        *,
        channel: str,
        channel_message_id: str,
        error_code: str,
    ) -> None:
        with self.session_factory() as session:
            row = self._find_by_key(session, channel, channel_message_id)
            if row is None:
                return
            row.status = ChannelMessageStatus.FAILED.value
            row.last_error = error_code[:64]
            row.updated_at = utcnow()
            session.commit()

    def counts(self) -> dict[str, dict[str, int]]:
        """Deliveries already recorded per channel, split by status."""
        with self.session_factory() as session:
            rows = session.execute(
                select(
                    ChannelMessage.channel,
                    ChannelMessage.status,
                    func.count(),
                ).group_by(ChannelMessage.channel, ChannelMessage.status)
            ).all()
        totals: dict[str, dict[str, int]] = {}
        for row in rows:
            totals.setdefault(str(row[0]), {})[str(row[1])] = int(row[2])
        return totals

    def _claim_existing(
        self,
        session: Session,
        row: ChannelMessage,
        digest: str,
    ) -> tuple[StoredDelivery, bool]:
        if row.request_digest != digest:
            raise ConflictError(
                "This channel message id was already used with different content.",
                code="CHANNEL_MESSAGE_ID_REUSED",
            )
        if row.status == ChannelMessageStatus.COMPLETED.value:
            return self._snapshot(row), False
        if row.status == ChannelMessageStatus.PROCESSING.value:
            age = (utcnow() - row.updated_at).total_seconds()
            if age < self.claim_timeout_seconds:
                raise ConflictError(
                    "This channel message is already being processed.",
                    code="CHANNEL_MESSAGE_IN_FLIGHT",
                    retryable=True,
                )
            row.status = ChannelMessageStatus.PROCESSING.value
            row.attempts += 1
            row.updated_at = utcnow()
            session.commit()
            return self._snapshot(row), True
        row.status = ChannelMessageStatus.PROCESSING.value
        row.attempts += 1
        row.last_error = None
        row.updated_at = utcnow()
        session.commit()
        return self._snapshot(row), True

    def _find(self, session: Session, envelope: MessageEnvelope) -> ChannelMessage | None:
        return self._find_by_key(
            session,
            envelope.channel.value,
            envelope.channel_message_id,
        )

    def _find_by_key(
        self,
        session: Session,
        channel: str,
        channel_message_id: str,
    ) -> ChannelMessage | None:
        return session.scalars(
            select(ChannelMessage).where(
                ChannelMessage.channel == channel,
                ChannelMessage.channel_message_id == channel_message_id,
            )
        ).one_or_none()

    def _snapshot(self, row: ChannelMessage) -> StoredDelivery:
        return StoredDelivery(
            channel=row.channel,
            channel_message_id=row.channel_message_id,
            status=row.status,
            response_payload=(
                dict(row.response_payload)
                if row.response_payload is not None
                else None
            ),
            rendered_text=row.rendered_text,
        )
