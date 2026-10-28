"""Why a channel can or cannot take inbound traffic. Observation only."""

from __future__ import annotations

from typing import get_args

from pydantic import SecretStr

from app.channels.base import ChannelAdapter, secret_is_configured
from app.channels.registry import INBOUND_IMPLEMENTED, WEBHOOK_INBOUND
from app.models.enums import Channel
from app.schemas.channels import ChannelAvailability

__all__ = ["AVAILABILITY_VALUES", "inbound_availability"]

# The closed value set, declared exactly once in ``app/schemas/channels.py``.
# Nothing here may add, rename or reorder a member.
AVAILABILITY_VALUES: tuple[ChannelAvailability, ...] = get_args(ChannelAvailability)


def _webhook_secret(adapter: ChannelAdapter) -> SecretStr | None:
    """The secret this adapter will actually verify an inbound request with.

    Read off the adapter instance, never re-derived from ``Settings``: the
    console adapter simply has no ``secret`` attribute, and a signing adapter
    holds the one value it passes to ``verify_hmac``. Widening through
    ``isinstance`` keeps the narrowed type honest under mypy strict.
    """
    secret = getattr(adapter, "secret", None)
    return secret if isinstance(secret, SecretStr) else None


def inbound_availability(
    channel: Channel, adapter: ChannelAdapter | None
) -> ChannelAvailability:
    """Say why this channel's inbound path is or is not usable.

    The four branches are evaluated in this fixed order and the first one that
    holds is the answer; the order is part of the contract and must not be
    shuffled. The result is an observation for the capability matrix only: it
    reads no ``Settings``, copies no credential check (it reuses
    ``secret_is_configured``, the same predicate ``verify_hmac`` fails on), and
    it changes nothing about routing, identity, idempotency or rendering.
    """
    if channel not in INBOUND_IMPLEMENTED:
        return "not_implemented"
    if adapter is None:
        return "not_enabled"
    if channel in WEBHOOK_INBOUND and not secret_is_configured(_webhook_secret(adapter)):
        return "webhook_secret_missing"
    return "available"
