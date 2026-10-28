from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, TypeGuard, cast

from pydantic import SecretStr

from app.core.errors import (
    DependencyUnavailableError,
    ForbiddenError,
    UnauthorizedError,
    ValidationAppError,
)
from app.core.security import Identity
from app.core.time import utcnow
from app.models.enums import Channel

__all__ = [
    "Attachment",
    "ChannelAdapter",
    "ChannelCapabilities",
    "InboundRequest",
    "MessageEnvelope",
    "RenderedMessage",
    "envelope_digest",
    "header",
    "json_object",
    "required_str",
    "resolve_identity",
    "secret_is_configured",
    "verify_hmac",
]


@dataclass(frozen=True, slots=True)
class Attachment:
    """Metadata only. The agent never receives raw bytes from a channel."""

    attachment_id: str
    filename: str
    content_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class MessageEnvelope:
    """The one shape every inbound message becomes before the agent sees it."""

    channel: Channel
    channel_message_id: str
    conversation_id: str | None
    identity: Identity
    text: str | None
    attachments: tuple[Attachment, ...] = ()
    locale: str = "zh-CN"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    received_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class ChannelCapabilities:
    """What a channel can carry. The renderer reads this, never the channel name."""

    supports_stream: bool
    supports_rich_card: bool
    supports_voice: bool
    max_length: int | None
    max_cards: int = 0
    sentence_char_limit: int | None = None
    formal: bool = False
    offers_human_handoff: bool = False


@dataclass(frozen=True, slots=True)
class InboundRequest:
    """A transport-neutral inbound request.

    ``identity`` is filled only by a server-side wrapper (the web route) after
    the dev header adapter ran. Webhooks never take identity from the body.
    """

    headers: Mapping[str, str]
    body: bytes
    query: Mapping[str, str] = field(default_factory=dict)
    identity: Identity | None = None


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    text: str
    payload: Mapping[str, Any]
    truncated: bool = False
    deduplicated: bool = False
    subject: str | None = None
    context_references: tuple[str, ...] = ()
    channel_message_id: str = ""

    @property
    def conversation_id(self) -> str | None:
        value = self.payload.get("conversation_id")
        return value if isinstance(value, str) else None

    @property
    def intent(self) -> str | None:
        value = self.payload.get("intent")
        return value if isinstance(value, str) else None

    def task_id(self) -> str | None:
        task = self.payload.get("task")
        if isinstance(task, Mapping):
            value = task.get("task_id")
            return value if isinstance(value, str) else None
        return None


class ChannelAdapter(Protocol):
    channel: Channel
    capabilities: ChannelCapabilities

    def verify(self, request: InboundRequest) -> None:
        """Reject a request that did not come from the channel provider."""
        ...

    def normalize(self, request: InboundRequest) -> MessageEnvelope:
        """Turn a verified transport payload into the shared envelope."""
        ...

    def render(
        self,
        response: Mapping[str, Any],
        *,
        deduplicated: bool = False,
    ) -> RenderedMessage:
        """Shape the canonical agent response for this channel."""
        ...


def header(request: InboundRequest, name: str) -> str:
    needle = name.lower()
    for key, value in request.headers.items():
        if key.lower() == needle:
            return value
    return ""


def json_object(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationAppError("Webhook body must be valid UTF-8 JSON.") from exc
    if not isinstance(data, dict):
        raise ValidationAppError("Webhook body must be a JSON object.")
    return cast(dict[str, Any], data)


def required_str(
    data: Mapping[str, Any],
    key: str,
    *,
    max_length: int = 2000,
) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationAppError(f"Webhook field '{key}' is required.")
    if len(value) > max_length:
        raise ValidationAppError(
            f"Webhook field '{key}' exceeds {max_length} characters."
        )
    return value.strip()


def resolve_identity(
    mapping: Mapping[str, str],
    sender: str,
    *,
    channel: Channel,
) -> Identity:
    """Map a provider sender to a tenant/user.

    The map lives in configuration, not in the payload: a signed sender id
    still cannot name its own tenant. An unmapped sender is rejected rather
    than defaulted.
    """
    raw = mapping.get(sender) or mapping.get(sender.lower())
    if not raw:
        raise ForbiddenError(
            f"No customer mapping for this {channel.value} sender."
        )
    tenant_id, separator, user_id = raw.partition(":")
    if not separator or not tenant_id.strip() or not user_id.strip():
        raise DependencyUnavailableError(
            f"The {channel.value} identity map is malformed; expected 'tenant:user'."
        )
    return Identity(
        tenant_id=tenant_id.strip(),
        user_id=user_id.strip(),
        roles=frozenset(),
    )


def secret_is_configured(secret: SecretStr | None) -> TypeGuard[SecretStr]:
    """Whether a signing secret is present and non-empty.

    The one place the "missing credential" test lives: ``verify_hmac`` below
    and the availability predicate in ``app/channels/availability.py`` both
    read it, so the two can never disagree about which secret counts.

    The return is a ``TypeGuard`` rather than a plain ``bool`` so the caller
    keeps the ``None`` narrowing it had when the test was written inline; the
    runtime value is exactly the negation of the old ``secret is None or not
    secret.get_secret_value()`` check.
    """
    return secret is not None and bool(secret.get_secret_value())


def verify_hmac(
    request: InboundRequest,
    secret: SecretStr | None,
    *,
    max_skew_seconds: int,
) -> None:
    """Verify ``X-Channel-Signature`` over ``timestamp + '.' + body``.

    The timestamp is inside the signed material, so replaying an old body
    with a fresh header fails. A missing secret is a configuration error
    (503), not a silent accept.
    """
    if not secret_is_configured(secret):
        raise DependencyUnavailableError(
            "This channel has no webhook secret configured."
        )
    signature = header(request, "x-channel-signature").strip()
    timestamp = header(request, "x-channel-timestamp").strip()
    if not signature:
        raise UnauthorizedError("Missing X-Channel-Signature.")
    if not timestamp:
        raise UnauthorizedError("Missing X-Channel-Timestamp.")
    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise UnauthorizedError("Invalid X-Channel-Timestamp.") from exc
    if max_skew_seconds > 0 and abs(int(time.time()) - sent_at) > max_skew_seconds:
        raise UnauthorizedError("Expired channel signature.")

    expected = hmac.new(
        secret.get_secret_value().encode("utf-8"),
        timestamp.encode("ascii") + b"." + request.body,
        hashlib.sha256,
    ).hexdigest()
    provided = signature.removeprefix("sha256=").lower()
    if not hmac.compare_digest(expected, provided):
        raise UnauthorizedError("Invalid channel signature.")


def envelope_digest(envelope: MessageEnvelope) -> str:
    """Fingerprint the meaning of a delivery, not its transport metadata."""
    material = {
        "channel": envelope.channel.value,
        "tenant_id": envelope.identity.tenant_id,
        "user_id": envelope.identity.user_id,
        "conversation_id": envelope.conversation_id,
        "text": envelope.text,
        "locale": envelope.locale,
        "metadata": dict(envelope.metadata),
        "attachments": [
            {
                "attachment_id": item.attachment_id,
                "filename": item.filename,
                "content_type": item.content_type,
                "size_bytes": item.size_bytes,
            }
            for item in envelope.attachments
        ],
    }
    canonical = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
