"""The outbound half of a channel: a notice that actually leaves the app.

Inbound adapters turn a provider payload into an agent turn. This module runs
the other direction: a confirmed proactive notice is rendered by the same
capability table, addressed from server-side configuration, and handed to a
transport. Three rules hold whatever the transport is:

* The target is never taken from the message. Tenant and user come from the
  queued row, the channel comes from the customer's own profile, and the
  address is the reverse of the provider-to-customer map in configuration. A
  payload that names an address is ignored.
* Consent is re-checked at send time. Opting out after confirming stops the
  message even though it is already queued.
* "Sent" means "handed to the configured transport". The transports here are
  one that refuses, one that is misconfigured, and one that appends to a local
  drop file, so a demonstration can show a rendered message without claiming a
  customer received anything. A provider protocol would be a fourth
  implementation of the same port, not a new branch in the queue.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels.base import RenderedMessage
from app.channels.render import render_notice
from app.core.config import Settings
from app.core.time import utcnow
from app.models.enums import Channel, NotificationStatus
from app.models.memory import CustomerProfile, Notification
from app.models.outbox import OutboxMessage
from app.observability.redaction import redact
from app.repositories.audit import AuditRepository

__all__ = [
    "PROACTIVE_EVENT_TYPE",
    "OutboundDeliveryError",
    "OutboundNotice",
    "OutboundTransport",
    "ProactiveSender",
    "build_transport",
    "resolve_address",
]

PROACTIVE_EVENT_TYPE = "proactive.confirmed"

_ADDRESS_MAPS: dict[Channel, str] = {
    Channel.EMAIL: "channel_email_identity_map",
    Channel.WECHAT: "channel_im_identity_map",
    Channel.WECOM: "channel_im_identity_map",
}


class OutboundDeliveryError(Exception):
    """The message did not leave. The queue keeps it and tries again.

    The reason code leads every message: it is what an operator reads in the
    queue's ``last_error``, and a refusal without one is not diagnosable.
    """

    def __init__(self, reason: str, message: str | None = None) -> None:
        detail = message or "delivery refused."
        super().__init__(f"[{reason}] {detail}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class OutboundNotice:
    """What a transport is asked to deliver. Rendered values, not a template."""

    notification_id: str
    tenant_id: str
    user_id: str
    channel: Channel
    address: str
    subject: str | None
    text: str
    truncated: bool
    event_type: str

    def record(self) -> dict[str, object]:
        """The line a drop file keeps. The address is part of the message."""
        return {
            "notification_id": self.notification_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "channel": self.channel.value,
            "address": self.address,
            "subject": self.subject,
            "text": self.text,
            "truncated": self.truncated,
            "event_type": self.event_type,
        }


class OutboundTransport(Protocol):
    """The seam a provider SDK would implement."""

    name: str

    def send(self, notice: OutboundNotice) -> None:
        """Deliver, or raise. Silence is not delivery."""
        ...


class RefusingTransport:
    """The default: nothing is wired, so nothing may claim to have been sent."""

    name = "none"

    def send(self, notice: OutboundNotice) -> None:
        raise OutboundDeliveryError(
            "NO_TRANSPORT",
            "CHANNEL_OUTBOUND_TRANSPORT=none: no outbound provider is wired, so "
            "the queue must not mark this message sent.",
        )


class MisconfiguredTransport:
    """Turned on without a drop file: every message refuses, loudly."""

    name = "local_file"

    def send(self, notice: OutboundNotice) -> None:
        raise OutboundDeliveryError(
            "DROP_FILE_MISSING",
            "CHANNEL_OUTBOUND_TRANSPORT=local_file requires "
            "CHANNEL_OUTBOUND_DROP_FILE.",
        )


class LocalFileTransport:
    """Append the rendered message to a local drop file.

    This stands in for a provider API; it is not one. It is honest about what
    happened: the bytes left the application and landed in a file an operator
    can open. It therefore writes the destination address, which is why the
    transport stays opt-in and is refused in production.
    """

    name = "local_file"

    def __init__(self, path: Path) -> None:
        self.path = path

    def send(self, notice: OutboundNotice) -> None:
        line = json.dumps(
            notice.record(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(os.fspath(self.path), "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise OutboundDeliveryError(
                "DROP_FILE_FAILED", f"Could not write the outbound drop file: {exc}."
            ) from exc


def build_transport(settings: Settings) -> OutboundTransport:
    if settings.channel_outbound_transport != "local_file":
        return RefusingTransport()
    if not settings.channel_outbound_drop_file:
        return MisconfiguredTransport()
    return LocalFileTransport(Path(settings.channel_outbound_drop_file))


def resolve_address(
    settings: Settings,
    *,
    channel: Channel,
    tenant_id: str,
    user_id: str,
) -> str:
    """Invert the provider-to-customer map to find where to send.

    The map is configuration, so a queued message cannot name its own
    destination. Channels without such a map have no outbound address, and a
    notice addressed to them is refused rather than guessed.
    """
    field = _ADDRESS_MAPS.get(channel)
    if field is None:
        raise OutboundDeliveryError(
            "NO_ADDRESS_SOURCE",
            f"Channel {channel.value} has no outbound address source.",
        )
    mapping = getattr(settings, field)
    wanted = f"{tenant_id}:{user_id}"
    for sender, owner in mapping.items():
        if owner == wanted:
            return str(sender)
    raise OutboundDeliveryError(
        "NO_ADDRESS",
        f"No {channel.value} account is mapped to this customer.",
    )


def _channel_of(profile: CustomerProfile) -> Channel:
    raw = (profile.preferences or {}).get("contact_channel")
    if not isinstance(raw, str) or not raw:
        raise OutboundDeliveryError(
            "NO_CHANNEL",
            "The customer profile does not name a channel to be reached on.",
        )
    try:
        return Channel(raw.upper())
    except ValueError as exc:
        raise OutboundDeliveryError(
            "UNKNOWN_CHANNEL", f"Stored contact channel {raw!r} is not a channel."
        ) from exc


class ProactiveSender:
    """Deliver one queued ``proactive.confirmed`` message or refuse it."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        transport: OutboundTransport | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.transport = transport if transport is not None else build_transport(settings)
        self.audit = AuditRepository(session)

    def deliver(self, row: OutboxMessage) -> None:
        if row.event_type != PROACTIVE_EVENT_TYPE:
            raise OutboundDeliveryError(
                "NOT_PROACTIVE", f"No outbound handler for {row.event_type}."
            )

        notification = self._notification(row)
        if notification.status == NotificationStatus.SENT.value:
            return
        if notification.status != NotificationStatus.CONFIRMED.value:
            raise OutboundDeliveryError(
                "NOT_CONFIRMED", "Only a confirmed notification may be sent."
            )

        profile = self._profile(row)
        if profile is None or not profile.proactive_opt_in:
            raise OutboundDeliveryError(
                "NO_CONSENT",
                "The customer is no longer opted in, so nothing is sent.",
            )
        if notification.event_type in (profile.muted_event_types or []):
            raise OutboundDeliveryError(
                "UNSUBSCRIBED",
                "The customer muted this event type after confirming it.",
            )

        channel = _channel_of(profile)
        rendered = self._render(channel, notification)
        address = resolve_address(
            self.settings,
            channel=channel,
            tenant_id=row.tenant_id,
            user_id=row.user_id,
        )
        self.transport.send(
            OutboundNotice(
                notification_id=notification.id,
                tenant_id=row.tenant_id,
                user_id=row.user_id,
                channel=channel,
                address=address,
                subject=rendered.subject,
                text=rendered.text,
                truncated=rendered.truncated,
                event_type=notification.event_type,
            )
        )

        notification.status = NotificationStatus.SENT.value
        notification.updated_at = utcnow()
        self.audit.record(
            actor_id=row.user_id,
            tenant_id=row.tenant_id,
            action="proactive.delivered",
            resource_type="notification",
            resource_id=notification.id,
            outcome="SUCCESS",
            detail={
                "channel": channel.value,
                "event_type": notification.event_type,
                "transport": self.transport.name,
                "chars": len(rendered.text),
                "truncated": rendered.truncated,
                "address_digest": hashlib.sha256(
                    address.encode("utf-8")
                ).hexdigest()[:12],
            },
        )

    def _notification(self, row: OutboxMessage) -> Notification:
        """Scoped by the queued row's owner, never by a payload claim."""
        notification_id = str(row.payload.get("notification_id", ""))
        notification = self.session.scalars(
            select(Notification)
            .where(
                Notification.id == notification_id,
                Notification.tenant_id == row.tenant_id,
                Notification.user_id == row.user_id,
            )
            .limit(1)
        ).one_or_none()
        if notification is None:
            raise OutboundDeliveryError(
                "NOT_FOUND", "The notification this message refers to is gone."
            )
        return notification

    def _profile(self, row: OutboxMessage) -> CustomerProfile | None:
        return self.session.scalars(
            select(CustomerProfile)
            .where(
                CustomerProfile.tenant_id == row.tenant_id,
                CustomerProfile.user_id == row.user_id,
            )
            .limit(1)
        ).one_or_none()

    def _render(self, channel: Channel, notification: Notification) -> RenderedMessage:
        return render_notice(
            channel,
            title=notification.title,
            body=redact(notification.body),
            sla_hours=self.settings.channel_email_sla_hours,
        )
