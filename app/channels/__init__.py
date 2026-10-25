"""Channel adapters: normalization, rendering and delivery idempotency.

Everything here is outside the cognition layer. A channel may change how a
message is verified, how identity is resolved and how a reply is shaped; it
must not change what the agent decides.
"""

from app.channels.base import (
    Attachment,
    ChannelAdapter,
    ChannelCapabilities,
    InboundRequest,
    MessageEnvelope,
    RenderedMessage,
)

__all__ = [
    "Attachment",
    "ChannelAdapter",
    "ChannelCapabilities",
    "InboundRequest",
    "MessageEnvelope",
    "RenderedMessage",
]
