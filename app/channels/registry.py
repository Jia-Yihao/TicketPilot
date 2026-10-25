from __future__ import annotations

from app.channels.base import ChannelAdapter
from app.channels.email import EmailChannelAdapter
from app.channels.im import ImChannelAdapter
from app.channels.web import WebChannelAdapter
from app.core.config import Settings
from app.models.enums import Channel

__all__ = ["build_adapters"]


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
