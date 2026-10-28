"""接管状态机与坐席侧队列：转人工之后到底发生什么。

转人工不是一种工单意图，而是会话进入的一种状态。``AGENT_ACTIVE`` 期间智能体
照常服务；一旦挂起移交，智能体就不再多说一个字，直到坐席接手或把会话交回。

    AGENT_ACTIVE --escalate--> HANDOFF_PENDING --claim--> HUMAN_ACTIVE
         ^                            |                      |
         |                            +--timeout--> QUEUED    |
         +---------release------------+----------------------+
    HUMAN_ACTIVE --close--> CLOSED

一条取舍：移交包与移交事件同一次写入。坐席第一屏看到的、审计留痕里记的，因此
永远是同一份东西——「少写一次库」不值当拿这个换。另一条：队列里的案卷
（含 ``NOTED``）和「接管中」的会话是两件事，前者坐席要看见，后者才让智能体闭嘴。

边界：本模块只认状态与队列，不判断「该不该转」（那是信号表与
``app/agent/support_agent.py`` 的判定），也不产出回复文案。它不碰任务与退款：
包里的 ``task_id`` 只是个指针，那个任务在不在、跑成什么样，由运行时说。
调用方是 ``support_agent.py`` 与 ``app/api/v1/handoffs.py``。
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.escalation import EscalationSignal
from app.agent.handoff import (
    ActionRecord,
    CandidateOrder,
    Citation,
    HandoffBundle,
    IntentRecord,
    SentimentSnapshot,
)
from app.core.errors import ConflictError, NotFoundError
from app.core.policy import PolicyEngine
from app.core.security import Identity
from app.core.time import utcnow
from app.models.conversation import Conversation, ConversationMessage
from app.models.enums import AbandonReason, Channel, HandoffStatus
from app.models.handoff import AbandonEvent, HandoffEvent
from app.observability.redaction import redact
from app.repositories.audit import AuditRepository
from app.services.memory import load_history_summary

__all__ = [
    "AbandonService",
    "HandoffMetrics",
    "HandoffService",
    "build_bundle",
]

TAKEOVER_STATES = (
    HandoffStatus.HANDOFF_PENDING,
    HandoffStatus.HUMAN_ACTIVE,
    HandoffStatus.QUEUED,
)

# The cases a seat still has to work. ``NOTED`` belongs here: it does not
# take the conversation over, but the seat has to see it.
OPEN_CASE_STATES = TAKEOVER_STATES + (HandoffStatus.NOTED,)

_TRANSITIONS: dict[HandoffStatus, dict[HandoffStatus, bool]] = {
    HandoffStatus.HANDOFF_PENDING: {
        HandoffStatus.HUMAN_ACTIVE: True,
        HandoffStatus.QUEUED: True,
        HandoffStatus.CLOSED: True,
        HandoffStatus.RELEASED: True,
    },
    HandoffStatus.QUEUED: {
        HandoffStatus.HUMAN_ACTIVE: True,
        HandoffStatus.CLOSED: True,
        HandoffStatus.RELEASED: True,
    },
    HandoffStatus.HUMAN_ACTIVE: {
        HandoffStatus.CLOSED: True,
        HandoffStatus.RELEASED: True,
    },
    HandoffStatus.NOTED: {HandoffStatus.CLOSED: True},
    HandoffStatus.CLOSED: {},
    HandoffStatus.RELEASED: {},
    HandoffStatus.AGENT_ACTIVE: {},
}

# Where the summary in the package came from. Named, so a reader of the
# package knows it is the customer's own words and not something the agent
# concluded about them.
_HISTORY_SUMMARY_SOURCE = "customer_profiles.history_summary"


def _now_iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _hour(moment: datetime | None) -> str:
    return f"{(moment or utcnow()).hour:02d}"


def _clip(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _message_to_dict(message: ConversationMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "intent": message.intent,
        "content": message.content,
        "payload": message.payload or {},
        "created_at": message.created_at,
    }


def build_bundle(
    *,
    identity: Identity,
    conversation: Conversation,
    signal: EscalationSignal,
    reason_detail: str,
    messages: list[ConversationMessage],
    spans: list[dict[str, Any]],
    slots: dict[str, Any],
    pending_task_id: str | None,
    sentiment: SentimentSnapshot,
    policy_refs: list[Citation],
    attempted_actions: list[ActionRecord],
    attempted_candidates: list[CandidateOrder] | None = None,
    digest_max_chars: int,
    channel: Channel = Channel.WEB,
) -> HandoffBundle:
    """Assemble the package a seat receives with the case."""
    intent_history: list[IntentRecord] = []
    turn = 0
    for message in messages:
        if message.role != "user":
            continue
        turn += 1
        payload = message.payload or {}
        intent_history.append(
            IntentRecord(
                turn=turn,
                intent=message.intent or "UNKNOWN",
                user_message=_clip(message.content, 120),
                outcome=str(payload.get("outcome", "UNKNOWN")),
                at=message.created_at.isoformat(),
            )
        )

    lines = [
        f"{record.turn}. 用户：{record.user_message}"
        f"（意图 {record.intent}，{record.outcome}）"
        for record in intent_history
    ]
    digest = "；".join(lines)

    return HandoffBundle(
        conversation_id=conversation.id,
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        channel=channel,
        reason=signal.reason,
        signal_id=signal.signal_id,
        priority=signal.priority,
        queue=signal.queue,
        transcript_digest=_clip(digest, digest_max_chars),
        transcript_ref=f"conversation://{conversation.id}/messages",
        intent_history=tuple(intent_history),
        slots=dict(slots),
        attempted_actions=tuple(attempted_actions),
        attempted_candidates=tuple(attempted_candidates or ()),
        pending_task_id=pending_task_id,
        sentiment=sentiment,
        policy_refs=tuple(policy_refs),
        suggested_next_step=signal.next_step,
        created_at=_now_iso(),
    )


class HandoffService:
    """Raises escalations, runs the takeover state machine, serves the queue."""

    def __init__(self, session: Session, *, policy: PolicyEngine) -> None:
        self.session = session
        self.policy = policy

    # ------------------------------------------------------------- reading
    def active_event(self, conversation_id: str) -> HandoffEvent | None:
        """The event that currently holds the conversation, if any."""
        rows = self.session.scalars(
            select(HandoffEvent)
            .where(
                HandoffEvent.conversation_id == conversation_id,
                HandoffEvent.status.in_([status.value for status in TAKEOVER_STATES]),
            )
            .order_by(HandoffEvent.created_at.desc(), HandoffEvent.id.desc())
        ).all()
        return rows[0] if rows else None

    def state(self, conversation_id: str) -> HandoffStatus:
        """会话当前属于谁：没有接管事件就是 ``AGENT_ACTIVE``。"""
        event = self.active_event(conversation_id)
        return HandoffStatus(event.status) if event else HandoffStatus.AGENT_ACTIVE

    def require_event(
        self, identity: Identity, event_id: str
    ) -> HandoffEvent:
        """按 id 取一个移交事件；跨租户与不存在都报同一种 not found。"""
        event = self.session.get(HandoffEvent, event_id)
        if event is None or event.tenant_id != identity.tenant_id:
            raise NotFoundError("Handoff not found.")
        return event

    def summary(self, event: HandoffEvent) -> dict[str, Any]:
        """The queue row: enough to triage, nothing the seat must decode."""
        bundle = event.bundle or {}
        return {
            "event_id": event.id,
            "conversation_id": event.conversation_id,
            "signal_id": event.signal_id,
            "reason": event.reason,
            "priority": event.priority,
            "queue": event.queue,
            "status": event.status,
            "intent": event.intent,
            "detail": event.detail,
            "channel": str(bundle.get("channel", Channel.WEB.value)),
            "claimed_by": event.claimed_by,
            "created_at": event.created_at.isoformat(),
            "updated_at": event.updated_at.isoformat(),
            "transcript_digest": str(bundle.get("transcript_digest", "")),
        }

    def detail(self, event: HandoffEvent) -> dict[str, Any]:
        """The case: the queue row plus the whole context package."""
        payload = self.summary(event)
        payload.update(
            {
                "user_id": event.user_id,
                "trace_id": event.trace_id,
                "pending_task_id": event.pending_task_id,
                "claimed_at": (
                    event.claimed_at.isoformat() if event.claimed_at else None
                ),
                "released_by": event.released_by,
                "closed_by": event.closed_by,
                "resolution": event.resolution,
                "bundle": event.bundle or {},
            }
        )
        return payload

    def list_queue(
        self,
        identity: Identity,
        *,
        statuses: list[HandoffStatus] | None = None,
        queue: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[HandoffEvent]:
        """列本租户的坐席队列，最新的在前；状态与队列都只是过滤条件。"""
        statement = select(HandoffEvent).where(HandoffEvent.tenant_id == identity.tenant_id)
        if statuses:
            statement = statement.where(
                HandoffEvent.status.in_([status.value for status in statuses])
            )
        if queue:
            statement = statement.where(HandoffEvent.queue == queue)
        statement = (
            statement.order_by(HandoffEvent.created_at.desc(), HandoffEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement))

    def count_queue(
        self,
        identity: Identity,
        *,
        statuses: list[HandoffStatus] | None = None,
        queue: str | None = None,
    ) -> int:
        """队列总数，过滤口径与 ``list_queue`` 完全一致，供分页用。"""
        statement = select(func.count()).select_from(HandoffEvent).where(
            HandoffEvent.tenant_id == identity.tenant_id
        )
        if statuses:
            statement = statement.where(
                HandoffEvent.status.in_([status.value for status in statuses])
            )
        if queue:
            statement = statement.where(HandoffEvent.queue == queue)
        return int(self.session.scalar(statement) or 0)

    # ------------------------------------------------------------- writing
    def escalate(
        self,
        identity: Identity,
        *,
        conversation: Conversation,
        signal: EscalationSignal,
        bundle: HandoffBundle,
        intent: str | None = None,
        detail: str = "",
        trace_id: str | None = None,
        pending_task_id: str | None = None,
    ) -> HandoffEvent:
        """Raise an escalation and put the conversation into takeover."""
        status = (
            HandoffStatus.HANDOFF_PENDING
            if signal.takeover
            else HandoffStatus.NOTED
        )
        bundle = self._with_history_summary(identity, bundle)
        event = HandoffEvent(
            id=f"hnd_{uuid.uuid4().hex[:16]}",
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            conversation_id=conversation.id,
            signal_id=signal.signal_id,
            reason=signal.reason.value,
            priority=signal.priority,
            queue=signal.queue,
            status=status.value,
            intent=intent,
            detail=detail,
            bundle=bundle.to_dict(),
            trace_id=trace_id,
            pending_task_id=pending_task_id,
        )
        self.session.add(event)
        self.session.flush()
        AuditRepository(self.session).record(
            actor_id=identity.user_id,
            tenant_id=identity.tenant_id,
            action="handoff.escalate",
            resource_type="handoff",
            resource_id=event.id,
            outcome="SUCCESS",
            detail={
                "signal_id": signal.signal_id,
                "reason": signal.reason.value,
                "queue": signal.queue,
                "takeover": signal.takeover,
            },
        )
        return event

    def claim(self, identity: Identity, event_id: str) -> HandoffEvent:
        """A seat takes the case over."""
        event = self.require_event(identity, event_id)
        if event.claimed_by and event.status == HandoffStatus.HUMAN_ACTIVE.value:
            raise ConflictError("This handoff is already claimed.")
        self._transition(event, HandoffStatus.HUMAN_ACTIVE)
        event.claimed_by = identity.user_id
        event.claimed_at = utcnow()
        self._audit(identity, event, "handoff.claim")
        return event

    def release(self, identity: Identity, event_id: str, *, note: str = "") -> HandoffEvent:
        """The seat hands the conversation back to the agent."""
        event = self.require_event(identity, event_id)
        self._transition(event, HandoffStatus.RELEASED)
        event.released_by = identity.user_id
        event.released_at = utcnow()
        self._audit(identity, event, "handoff.release", {"note": note})
        return event

    def close(
        self, identity: Identity, event_id: str, *, resolution: str = "RESOLVED"
    ) -> HandoffEvent:
        """The case is finished."""
        event = self.require_event(identity, event_id)
        self._transition(event, HandoffStatus.CLOSED)
        event.closed_by = identity.user_id
        event.closed_at = utcnow()
        event.resolution = resolution
        self._audit(identity, event, "handoff.close", {"resolution": resolution})
        return event

    def expire_pending(self, now: datetime | None = None) -> int:
        """Move handoffs nobody claimed into the queue.

        A pending handoff that nobody picks up is not a failure: it becomes a
        queued case with a callback promise instead of a silent drop.
        """
        moment = now or utcnow()
        deadline = moment - timedelta(seconds=self.policy.escalation.claim_timeout_seconds)
        rows = self.session.scalars(
            select(HandoffEvent).where(
                HandoffEvent.status == HandoffStatus.HANDOFF_PENDING.value,
                HandoffEvent.created_at <= deadline,
            )
        ).all()
        for event in rows:
            event.status = HandoffStatus.QUEUED.value
        self.session.flush()
        return len(rows)

    # ------------------------------------------------------------- helpers
    def _with_history_summary(
        self, identity: Identity, bundle: HandoffBundle
    ) -> HandoffBundle:
        """Put the customer's own summary into the package, if there is one.

        The identity here is the person the conversation belongs to, not the
        seat reading the case. The value is masked before it is clipped, and
        the clip is the same one — and the same limit — as the transcript
        digest, so the package never carries more of the customer's words
        than the case screen can show.
        """
        raw = load_history_summary(self.session, identity)
        if raw is None:
            return bundle
        limit = self.policy.escalation.digest_max_chars
        summary = _clip(redact(raw), limit)
        if not summary:
            return bundle
        return replace(
            bundle,
            history_summary=summary,
            history_summary_source=_HISTORY_SUMMARY_SOURCE,
            history_summary_max_chars=limit,
        )

    def _transition(self, event: HandoffEvent, target: HandoffStatus) -> None:
        current = HandoffStatus(event.status)
        allowed = _TRANSITIONS.get(current, {})
        if not allowed.get(target, False):
            raise ConflictError(
                f"Handoff is {current.value}; it cannot move to {target.value}."
            )
        event.status = target.value

    def _audit(
        self,
        identity: Identity,
        event: HandoffEvent,
        action: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        AuditRepository(self.session).record(
            actor_id=identity.user_id,
            tenant_id=identity.tenant_id,
            action=action,
            resource_type="handoff",
            resource_id=event.id,
            outcome="SUCCESS",
            detail=detail or {},
        )


class AbandonService:
    """Detects conversations that ended without a resolution.

    Abandonment is the failure nobody notices: the user got neither an answer
    nor a human. Two shapes are recorded — a conversation that went quiet
    without a resolution, and a user who comes back with the same question
    within the repeat window.
    """

    def __init__(self, session: Session, *, policy: PolicyEngine) -> None:
        self.session = session
        self.policy = policy

    def record(
        self,
        identity: Identity,
        *,
        conversation_id: str,
        intent: str | None,
        reason: AbandonReason,
        idle_minutes: int = 0,
        detail: dict[str, Any] | None = None,
        channel: Channel = Channel.WEB,
    ) -> AbandonEvent:
        """记一条弃单事实：客户既没拿到答案，也没等到人。"""
        event = AbandonEvent(
            id=f"abd_{uuid.uuid4().hex[:16]}",
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            conversation_id=conversation_id,
            intent=intent,
            channel=channel.value,
            reason=reason.value,
            detail=detail or {},
            idle_minutes=idle_minutes,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def detect_reentry(
        self,
        identity: Identity,
        *,
        intent: str,
        exclude_conversation_id: str | None = None,
        channel: Channel = Channel.WEB,
    ) -> AbandonEvent | None:
        """Record an abandonment when the same question comes back unresolved.

        The conversation the user is in right now is excluded: answering a
        follow-up question in the same thread is not an abandonment.
        """
        window = timedelta(hours=self.policy.escalation.repeat_window_hours)
        deadline = utcnow() - window
        rows = self.session.scalars(
            select(Conversation).where(
                Conversation.tenant_id == identity.tenant_id,
                Conversation.user_id == identity.user_id,
                Conversation.updated_at >= deadline,
            )
        ).all()
        for conversation in rows:
            if conversation.id == exclude_conversation_id:
                continue
            slots = conversation.slots or {}
            if slots.get("last_intent") != intent or slots.get("resolved", True):
                continue
            return self.record(
                identity,
                conversation_id=conversation.id,
                intent=intent,
                reason=AbandonReason.REPEAT_INTENT,
                idle_minutes=0,
                detail={
                    "previous_conversation_id": conversation.id,
                    "window_hours": self.policy.escalation.repeat_window_hours,
                },
                channel=channel,
            )
        return None

    def list_events(
        self, identity: Identity, *, limit: int = 100, offset: int = 0
    ) -> list[AbandonEvent]:
        """本租户的弃单事件，最新的在前；只读，不分页之外不做加工。"""
        statement = (
            select(AbandonEvent)
            .where(AbandonEvent.tenant_id == identity.tenant_id)
            .order_by(AbandonEvent.created_at.desc(), AbandonEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement))

    def count_events(self, identity: Identity) -> int:
        """本租户弃单事件总数，供列表分页。"""
        statement = select(func.count()).select_from(AbandonEvent).where(
            AbandonEvent.tenant_id == identity.tenant_id
        )
        return int(self.session.scalar(statement) or 0)


class HandoffMetrics:
    """The numbers a service manager watches.

    Every rate is scoped to one tenant and measured per conversation, so a
    tenant that escalates a lot cannot move another tenant's number.
    ``auto_resolution_rate`` counts the conversations the agent closed by
    itself: no handoff event and the last turn ended resolved.
    """

    def __init__(self, session: Session, *, policy: PolicyEngine) -> None:
        self.session = session
        self.policy = policy

    def collect(
        self, identity: Identity, *, window_minutes: int | None = None
    ) -> dict[str, Any]:
        """现算一组运营指标：会话量、转人工率、自动解决率、队列与小时分布。

        ``window_minutes`` 是时间窗；不给就是全量。分母始终是窗口内的会话数，
        不是历史累计——拿累计做分母，任何一次运营事故都会被时间稀释掉。
        """
        since = (
            utcnow() - timedelta(minutes=window_minutes)
            if window_minutes is not None
            else None
        )
        conversation_query = select(Conversation.id, Conversation.slots).where(
            Conversation.tenant_id == identity.tenant_id
        )
        event_query = select(
            HandoffEvent.conversation_id,
            HandoffEvent.signal_id,
            HandoffEvent.reason,
            HandoffEvent.queue,
            HandoffEvent.status,
            HandoffEvent.created_at,
        ).where(HandoffEvent.tenant_id == identity.tenant_id)
        abandon_query = select(
            AbandonEvent.intent,
            AbandonEvent.channel,
            AbandonEvent.created_at,
        ).where(AbandonEvent.tenant_id == identity.tenant_id)
        if since is not None:
            # The window counts conversations still active in it and events
            # created in it; a lifetime denominator would flatten the rate.
            conversation_query = conversation_query.where(
                Conversation.updated_at >= since
            )
            event_query = event_query.where(HandoffEvent.created_at >= since)
            abandon_query = abandon_query.where(AbandonEvent.created_at >= since)
        conversations = self.session.execute(conversation_query).all()
        events = self.session.execute(event_query).all()
        abandons = self.session.execute(abandon_query).all()

        escalated = {row.conversation_id for row in events}
        open_statuses = {status.value for status in OPEN_CASE_STATES}
        auto_resolved = sum(
            1
            for row in conversations
            if row.id not in escalated and (row.slots or {}).get("resolved", False)
        )

        by_queue: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        by_signal: dict[str, int] = {}
        by_hour: dict[str, int] = {}
        for event in events:
            by_queue[event.queue] = by_queue.get(event.queue, 0) + 1
            by_reason[event.reason] = by_reason.get(event.reason, 0) + 1
            by_signal[event.signal_id] = by_signal.get(event.signal_id, 0) + 1
            hour = _hour(event.created_at)
            by_hour[hour] = by_hour.get(hour, 0) + 1

        abandon_by_channel: dict[str, int] = {}
        abandon_by_intent: dict[str, int] = {}
        abandon_by_hour: dict[str, int] = {}
        for abandon in abandons:
            channel = abandon.channel or "UNKNOWN"
            intent = abandon.intent or "UNKNOWN"
            abandon_by_channel[channel] = abandon_by_channel.get(channel, 0) + 1
            abandon_by_intent[intent] = abandon_by_intent.get(intent, 0) + 1
            hour = _hour(abandon.created_at)
            abandon_by_hour[hour] = abandon_by_hour.get(hour, 0) + 1

        total_conversations = len(conversations)
        abandon_events = len(abandons)
        return {
            "window_minutes": window_minutes,
            "conversations": total_conversations,
            "handoff_conversations": len(escalated),
            "handoff_rate": _rate(len(escalated), total_conversations),
            "auto_resolution_rate": _rate(auto_resolved, total_conversations),
            "open_cases": sum(
                1 for event in events if event.status in open_statuses
            ),
            "by_queue": dict(sorted(by_queue.items())),
            "by_reason": dict(sorted(by_reason.items())),
            "by_signal": dict(sorted(by_signal.items())),
            "by_hour": dict(sorted(by_hour.items())),
            "abandon_events": abandon_events,
            "abandon_rate": _rate(abandon_events, total_conversations),
            "abandon_by_channel": dict(sorted(abandon_by_channel.items())),
            "abandon_by_intent": dict(sorted(abandon_by_intent.items())),
            "abandon_by_hour": dict(sorted(abandon_by_hour.items())),
        }
