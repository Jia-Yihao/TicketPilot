from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class VerificationStatus(StrEnum):
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    FAILED = "FAILED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CONSUMED = "CONSUMED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INVALID = "INVALID"
    CANCELLED = "CANCELLED"


class ToolExecutionStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    UNKNOWN = "UNKNOWN"
    RECONCILED = "RECONCILED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class OutboxStatus(StrEnum):
    """Where a queued message stands.

    ``PENDING`` waits for a worker, ``PROCESSING`` is claimed by one,
    ``SENT`` landed, ``FAILED`` is the dead letter and ``CANCELLED`` was
    withdrawn before it was sent.
    """

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SENT = "SENT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class OrderStatus(StrEnum):
    PLACED = "PLACED"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class LogisticsStatus(StrEnum):
    PENDING = "PENDING"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    EXCEPTION = "EXCEPTION"


class RefundStatus(StrEnum):
    REQUESTED = "REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class RiskLevel(StrEnum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"


class Intent(StrEnum):
    POLICY_QA = "POLICY_QA"
    ORDER_QUERY = "ORDER_QUERY"
    LOGISTICS_QUERY = "LOGISTICS_QUERY"
    TICKET_CREATE = "TICKET_CREATE"
    REFUND_REQUEST = "REFUND_REQUEST"
    UNKNOWN = "UNKNOWN"


class Channel(StrEnum):
    """Where a conversation happens.

    The enum is the adapter seam: adding a channel changes verification,
    identity mapping and rendering, not the intent or policy layer.
    """

    WEB = "WEB"
    APP = "APP"
    EMAIL = "EMAIL"
    WECHAT = "WECHAT"
    WECOM = "WECOM"
    WHATSAPP = "WHATSAPP"
    VOICE = "VOICE"
    SMS = "SMS"


class ChannelMessageStatus(StrEnum):
    """Delivery state of one inbound channel message."""

    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class NotificationStatus(StrEnum):
    """Where a proactive proposal stands."""

    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    SENT = "SENT"


class ProactiveEventType(StrEnum):
    """Events a rule may listen to. A rule cannot invent a new event."""

    REFUND_SETTLED = "REFUND_SETTLED"
    TICKET_UPDATED = "TICKET_UPDATED"
    DELIVERY_EXCEPTION = "DELIVERY_EXCEPTION"


class HandoffReason(StrEnum):
    """Why the agent stopped serving and reached for a human."""

    USER_REQUEST = "USER_REQUEST"
    FRUSTRATION = "FRUSTRATION"
    NO_EVIDENCE = "NO_EVIDENCE"
    REPEAT_FAILURE = "REPEAT_FAILURE"
    NO_UNDERSTANDING = "NO_UNDERSTANDING"
    RISK = "RISK"
    COMPLAINT = "COMPLAINT"
    IDLE_TIMEOUT = "IDLE_TIMEOUT"
    OTHER = "OTHER"


class HandoffStatus(StrEnum):
    """The takeover state machine.

    ``AGENT_ACTIVE`` is the absence of a takeover; the other states are what
    a handoff event may hold.
    """

    AGENT_ACTIVE = "AGENT_ACTIVE"
    HANDOFF_PENDING = "HANDOFF_PENDING"
    HUMAN_ACTIVE = "HUMAN_ACTIVE"
    QUEUED = "QUEUED"
    CLOSED = "CLOSED"
    RELEASED = "RELEASED"
    NOTED = "NOTED"


class AbandonReason(StrEnum):
    """Why a conversation is considered abandoned rather than answered."""

    IDLE_NO_RESOLUTION = "IDLE_NO_RESOLUTION"
    REPEAT_INTENT = "REPEAT_INTENT"
