from __future__ import annotations

from app.channels.base import ChannelAdapter
from app.channels.email import EmailChannelAdapter
from app.channels.im import ImChannelAdapter
from app.channels.web import WebChannelAdapter
from app.core.config import Settings
from app.models.enums import Channel

__all__ = ["INBOUND_IMPLEMENTED", "WEBHOOK_INBOUND", "build_adapters"]

# Which channels have an inbound path in *this codebase*. Nothing more: it says
# nothing about configuration switches or about a provider's own protocol, and
# it is the single source of truth for both the availability predicate
# (``app/channels/availability.py``) and the signed webhook allowlist
# (``app/channels/gateway.py``). Adding a channel here without building its
# adapter in ``build_adapters`` below — or the other way round — breaks the
# inbound availability contract.
INBOUND_IMPLEMENTED: frozenset[Channel] = frozenset(
    {Channel.WEB, Channel.EMAIL, Channel.WECHAT, Channel.WECOM}
)
# The inbound channels that arrive through a signed provider callback, i.e.
# everything above except the console's own path.
WEBHOOK_INBOUND: frozenset[Channel] = INBOUND_IMPLEMENTED - {Channel.WEB}


def build_adapters(settings: Settings) -> dict[Channel, ChannelAdapter]:
    """Only enabled channels are reachable; the rest are a 404, not a 500."""
    adapters: dict[Channel, ChannelAdapter] = {}
    if settings.channel_web_enabled:
        adapters[Channel.WEB] = WebChannelAdapter(sla_hours=settings.channel_email_sla_hours)
    if settings.channel_email_enabled:
        adapters[Channel.EMAIL] = EmailChannelAdapter(
            secret=settings.channel_email_secret,
            identity_map=settings.channel_email_identity_map,
            max_skew_seconds=settings.channel_webhook_max_skew_seconds,
            sla_hours=settings.channel_email_sla_hours,
        )
    if settings.channel_im_enabled:
        for channel in (Channel.WECHAT, Channel.WECOM):
            adapters[channel] = ImChannelAdapter(
                channel=channel,
                secret=settings.channel_im_secret,
                identity_map=settings.channel_im_identity_map,
                max_skew_seconds=settings.channel_webhook_max_skew_seconds,
                sla_hours=settings.channel_email_sla_hours,
            )
    # Capabilities are declared even for disabled channels, so the matrix
    # endpoint can explain what a channel would do before it is switched on.
    return adapters
