"""只读客服：把一句话装配成一个回合，只回答，不动业务数据。

一个回合的顺序是固定的：规则先读（意图目录 + 关键词表）；规则读不出来时才
允许问一次模型，且只采用「退款」这一种模型读法；答复由动作处理器生成，
最后必须过输出护栏。缺哪个槽位就追问哪个，绝不用默认值顶上。

第一条边界：这里只编排**读**。退款、建单这类写请求原样交给
``app.services.runtime.TaskRuntime``——审批、幂等、台账、审计都在那里，
本模块不给自己留任何写业务表的旁路。

第二条边界：会话接管的状态机不在这里（见 ``app/services/handoff.py``）。
本模块只问两件事：「现在该不该沉默」「现在该不该转人工」，不自己维护状态。

调用方是 HTTP 层（``app/api/v1/support.py``）和渠道网关
（``app/channels/gateway.py``）。反方向不成立：任务运行时、仓储与工具实现
都不该依赖本模块——它们要的是一个回合留下的任务与台账，那是数据库里的事实。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session, sessionmaker

from app.agent import actions
from app.agent.actions import ActionRequest
from app.agent.analytics_narrator import build_analytics_narrator
from app.agent.counter_question import get_counter_question
from app.agent.decision import ModelDecision, request_digest
from app.agent.escalation import EscalationSignal, get_escalation_catalog
from app.agent.handoff import ActionRecord, CandidateOrder, Citation
from app.agent.intent_catalog import IntentCatalog, get_catalog
from app.agent.personalization import (
    EFFECT_FIELDS,
    load_profile,
    personalize,
)
from app.agent.pydantic_agent import ADOPTABLE_INTENTS
from app.agent.refund_reason_rules import match_refund_reason
from app.agent.routing import (
    RULESET_FINGERPRINT,
    RoutePlan,
    _refund_reason,
    blocks_write_action,
    classify,
    extract_amount,
    extract_order_id,
    strip_amount,
)
from app.agent.turns import Draft, SupportIntent, SupportPlan, Turn
from app.core.config import Settings
from app.core.guard import SecurityAction, SecurityCategory, SecuritySeverity, scan_input
from app.core.output_guard import guard_output
from app.core.policy import PolicyEngine
from app.core.security import Identity
from app.core.time import utcnow
from app.models.conversation import Conversation
from app.models.enums import AbandonReason, Channel, HandoffStatus, Intent, Outcome
from app.models.handoff import HandoffEvent
from app.models.security_event import SecurityEvent
from app.observability import tracing
from app.observability.redaction import digest
from app.observability.tracing import TraceRecorder, reply_digest
from app.repositories.commerce import CommerceRepository
from app.repositories.traces import TraceRepository
from app.retrieval.build import build_policy_search
from app.services.catalog import CatalogService
from app.services.conversations import (
    ConversationService,
    ConversationSlots,
    ConversationTurn,
)
from app.services.escalation import (
    EscalationDecision,
    EscalationEngine,
    EscalationState,
)
from app.services.handoff import (
    TAKEOVER_STATES,
    AbandonService,
    HandoffService,
    build_bundle,
)
from app.services.llm_usage import LlmUsageService
from app.services.runtime import TaskRuntime
from app.services.security_events import SecurityEventService
from app.services.support import (
    CANDIDATE_LIMIT,
    SupportContextService,
    order_candidate,
)
from app.sql.narrative import AnalyticsNarrator
from app.sql.planner import names_dataset
from app.sql.router import asks_for_detail_followup
from app.sql.service import SqlAnalyticsService

if TYPE_CHECKING:
    from app.agent.pydantic_agent import ModelTrace, QuestionProbe

__all__ = [
    "SupportAgent",
    "SupportIntent",
    "SupportPlan",
    "Draft",
    "Turn",
    "classify_support",
    "model_gate",
]

logger = logging.getLogger(__name__)


def model_gate(plan: SupportPlan, route: RoutePlan, message: str) -> str:
    """The three-level routing decision, written down before any call.

    ``rules_hit`` — the rules already read the turn; the model has nothing to
    add and is not asked.
    ``question`` — the rules are blind, but the message asks for information
    instead of requesting an action. No model reading could be adopted for a
    read question, so the call would buy nothing.
    ``consult`` — a genuine gap: rules blind, action-shaped message. Only here
    may the model be asked, and only a refund reading is adopted.
    """
    gap_readable = plan.intent is SupportIntent.UNKNOWN or (
        route.intent is Intent.UNKNOWN and plan.intent is SupportIntent.ORDER_QUERY
    )
    if not gap_readable:
        return "rules_hit"
    if blocks_write_action(message.lower()):
        return "question"
    return "consult"

_TOOL_SOURCED_ACTIONS = frozenset(
    {
        "catalog.search",
        "order.get",
        "order.logistics",
        "order.delivery_review",
        "refund.eligibility",
        "refund.status",
        "analytics.query",
    }
)


def _clarify_candidate_count(draft: Draft) -> int:
    """How many order candidates this asking showed.

    The ledger stores the number, not the order numbers: a trace is read by
    seats, sampled into eval files and shipped to the bad-case queue, while a
    candidate list is personal data that only belongs to this reply.
    """
    if draft.clarification is None:
        return 0
    return sum(1 for card in draft.cards if card.get("kind") == "order_candidate")


# The clarification reason a formed counter-question rides on (口径
# ``.qoder/rules/clarify-counter-question.md`` §6.1): a new *value* on the
# existing ``clarify_reason`` trace key, not a new key.
COUNTER_QUESTION_REASON = "COUNTER_QUESTION_INTENT_AMBIGUOUS"

# The handoff tail sentence, verbatim from ``_clarify`` (§4.5): the
# counter-question reuses the one sentence the product already says instead
# of writing a second promise the customer could hold us to.
_HANDOFF_TAIL = "你也可以直接说「转人工」，我会转给人工同事接手。"

# Today's question-gate gap, kept byte-identical as the fallback (§3.2): a
# question round that does not become a counter-question must not look like
# one, even when the probe already paid for the call.
_QUESTION_GAP: dict[str, Any] = {
    "source": "rules",
    "intent": "UNKNOWN",
    "adopted": False,
    "gate": "question",
    # 没成反问就没有发生过任何采纳判定：既没有采纳的改道，也没有被拒的改道。
    "redirect": False,
    "redirect_rejected": False,
    "redirect_kind": "none",
}


def _counter_question_draft(probe: QuestionProbe, plan: SupportPlan) -> Draft | None:
    """Compose the one counter-question a successful probe buys (§4, §6.1).

    ``None`` whenever the question would come out half-built: a missing
    display name for the reading or for any option to be shown is refused
    rather than papered over, because a truncated question misleads more
    than no question at all (§4.7). Options keep the candidate set's returned
    order — reordering would silently restate the server's evidence — drop
    ``UNKNOWN`` (its customer language is the fallback tail), and stop at the
    data value ``max_options``. No model output text enters any string here.
    """
    spec = get_counter_question()
    if probe.intent is None or probe.candidate_set is None:
        return None
    if spec.labels.get(probe.intent) is None:
        return None
    labels: list[str] = []
    for candidate in probe.candidate_set.candidates:
        if len(labels) >= spec.max_options:
            break
        if candidate.intent_id == SupportIntent.UNKNOWN.value:
            continue
        label = spec.labels.get(candidate.intent_id)
        if label is None:
            return None
        labels.append(label)
    if not labels:
        return None
    return Draft(
        # The *rule* reading, never the bare probe row name: a counter-question
        # confirms, it does not route (§6.1; the reading may not even be an
        # ``Intent`` member).
        intent=plan.intent,
        reply=f"{spec.prompt_template}{'、'.join(labels)}。{_HANDOFF_TAIL}",
        suggestions=labels,
        clarification={
            "reason": COUNTER_QUESTION_REASON,
            "prompt": spec.prompt_template,
            "expected_slot": None,
            # ``None``, never the probe reading: the pending stack resumes a
            # *confirmed* intent, and nothing here is confirmed yet (§6.1).
            "resumable_intent": None,
        },
        resolved=False,
    )


def classify_support(
    message: str,
    route: RoutePlan,
    *,
    has_catalog_match: bool,
    message_order_id: str | None = None,
    order_id: str | None = None,
) -> SupportPlan:
    """Pick the intent for a read-path message.

    The catalog decides: every row carries its own cues, and the rows are
    tried most specific first. A collaboration row (asking for a human) is
    not a write action, so it outranks the ticket path.
    """
    text = message.lower()
    resolved_order = order_id or route.order_id
    spec = get_catalog().match(
        text,
        has_catalog_match=has_catalog_match,
        has_order_id=bool(message_order_id),
    )
    if spec is not None and spec.nature == "collaboration":
        return SupportPlan(SupportIntent(spec.intent_id), resolved_order)
    if route.intent in {Intent.REFUND_REQUEST, Intent.TICKET_CREATE} and not (
        blocks_write_action(text)
    ):
        return SupportPlan(SupportIntent(route.intent.value), resolved_order)
    if spec is None:
        return SupportPlan(SupportIntent.UNKNOWN, resolved_order)
    return SupportPlan(SupportIntent(spec.intent_id), resolved_order)


@dataclass(frozen=True, slots=True)
class _SlotReading:
    """一句话被读出来的槽位，以及规则表与意图目录各自给出的意图。"""

    message_order: str | None
    message_amount: int | None
    amount_invalid: bool
    api_order: str | None
    resolved_order: str | None
    resolved_amount: int | None
    context: dict[str, Any]
    route: RoutePlan
    plan: SupportPlan
    product: Any


@dataclass(frozen=True, slots=True)
class _Consultation:
    """模型咨询这一段的产出：问了没有、问到了什么、最后采用了哪一份读法。"""

    plan: SupportPlan
    route: RoutePlan
    context: dict[str, Any]
    resumed: bool
    expand_query_id: str | None
    model_gap: dict[str, Any] | None
    model_trace: ModelTrace | None
    model_decision: ModelDecision | None
    # 问句门谈成的一次反问草稿（口径 §6.1）；其余出口恒为 ``None``。
    counter_draft: Draft | None
    resealed_keys: list[str]


class SupportAgent:
    """Deterministic local customer-service agent.

    Read-only answers are composed outside the task runtime so they do not
    pollute the action ledger. Refund and ticket requests still go through
    TaskRuntime and keep approvals, idempotency and audit guarantees.

    The intent catalog decides what a message means and which action serves
    it; this class only sequences the turn: read the slots, ask for what is
    missing, then hand over to the action.

    Conversation slots are persisted per tenant and user, so a follow-up
    turn can inherit the order id from the previous turn and a missing slot
    can be asked for exactly once before the request is resumed.

    The trigger table decides when the agent stops serving: a signal fires,
    the conversation is handed to a human with a context bundle, and the
    agent stays silent until a seat gives the conversation back.
    """

    mode = "local"

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        runtime: TaskRuntime,
        *,
        catalog: CatalogService | None = None,
        policy: PolicyEngine | None = None,
        analytics: SqlAnalyticsService | None = None,
        narrator: AnalyticsNarrator | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.runtime = runtime
        self.catalog = catalog or CatalogService()
        self.policy = policy or PolicyEngine.from_settings(settings)
        self.analytics = analytics or SqlAnalyticsService(
            max_limit=settings.analytics_max_limit,
            prefetch_enabled=settings.analytics_prefetch_enabled,
            prefetch_max_rows=settings.analytics_prefetch_max_rows,
            snapshot_ttl_seconds=settings.analytics_snapshot_ttl_seconds,
        )
        # ``None`` is a promise, not an omission: rules mode and the disabled
        # switch cannot reach a model because there is nothing to call.
        self.narrator = narrator or build_analytics_narrator(settings)
        self.escalation = EscalationEngine(
            get_escalation_catalog(), self.policy
        )

    def handle(
        self,
        identity: Identity,
        message: str,
        *,
        conversation_id: str | None = None,
        order_id: str | None = None,
        channel: Channel = Channel.WEB,
    ) -> dict[str, Any]:
        """Run one turn. ``channel`` is provenance only: it labels the
        handoff bundle and abandon events, never a decision branch."""
        with self.session_factory() as session:
            conversations = ConversationService(session)
            turn = conversations.resolve(identity, conversation_id)
            session.commit()
            carried = turn.slots
            guard_events = self._guard_input(
                session, identity, turn.conversation.id, message
            )

            handoffs = HandoffService(session, policy=self.policy)
            handoff_state = handoffs.state(turn.conversation.id)
            if handoff_state in TAKEOVER_STATES:
                return self._silent(
                    session,
                    conversations,
                    turn,
                    identity,
                    message,
                    handoff_state,
                    handoffs,
                )

            # --- 阶段二：把这一句读成槽位与意图 ---
            # The understand segment starts here, before the recorder exists
            # (R9 口径 §2.1.5): support.classify must carry this reading, not
            # the recorder's construction clock.
            classify_started = time.perf_counter()
            reading = self._stage_read_slots(message, order_id, channel, carried)
            # --- 阶段三：决定最终读法（沿用追问 / 展开统计 / 问一次模型）---
            consult = self._stage_consult_model(
                session, identity, message, reading, carried, turn
            )
            message_order = reading.message_order
            message_amount = reading.message_amount
            amount_invalid = reading.amount_invalid
            api_order = reading.api_order
            resolved_order = reading.resolved_order
            resolved_amount = reading.resolved_amount
            product = reading.product
            route = consult.route
            context = consult.context
            plan = consult.plan
            resumed = consult.resumed
            expand_query_id = consult.expand_query_id
            model_gap = consult.model_gap
            model_decision = consult.model_decision
            counter_draft = consult.counter_draft
            resealed_keys = consult.resealed_keys

            # --- 阶段四：落 ledger 与回合状态（trace 从这里开始记）---
            catalog = get_catalog()
            trace = TraceRecorder(
                tenant_id=identity.tenant_id,
                conversation_id=turn.conversation.id,
                root_name="support.turn",
            )
            trace.add(
                "understand",
                "support.classify",
                attributes={
                    "intent": plan.intent.value,
                    "route_intent": route.intent.value,
                    "model_gap": model_gap,
                    "order_id": resolved_order,
                    "amount_cents": resolved_amount,
                    "resumed": resumed,
                    "analytics_expand": expand_query_id,
                    "product_id": product.product_id if product else None,
                    "risk_level": catalog.risk_of(plan.intent.value).value,
                    "catalog_version": catalog.version,
                    "guard_categories": [event.category for event in guard_events],
                },
                latency_ms=tracing.elapsed_ms(classify_started),
            )
            if resealed_keys:
                # The marker the re-seal owes the reader (ADR-030 决策 6): this
                # turn's digest was recomputed over keys that entered the
                # context *after* the model answered, so the record must not
                # claim the model saw them. Key names only — no values, no
                # order numbers, never the reply text or a model prompt, and
                # nothing here is one of the six fields the ledger verifies.
                trace.add(
                    "understand",
                    "decision.reseal",
                    attributes={"derived_keys": sorted(resealed_keys)},
                )

            state = Turn(
                conversation_id=turn.conversation.id,
                order_id=resolved_order,
                amount_cents=resolved_amount,
                product_id=product.product_id if product else carried.product_id,
                refund_reason=route.refund_reason,
                refund_type=route.refund_type,
                inherited=self._inherited_slots(
                    carried,
                    message_order=message_order,
                    api_order=api_order,
                    message_amount=message_amount,
                    resolved_order=resolved_order,
                    resolved_amount=resolved_amount,
                ),
            )

            # --- 阶段五：回合前判一次要不要现在就转人工 ---
            escalation_state, decision = self._stage_escalation_pre(
                carried, message, plan, resolved_amount, catalog
            )

            # --- 阶段六：缺槽位就追问，否则派给动作处理器 ---
            present: set[str] = set()
            if resolved_order:
                present.add("order_id")
            if resolved_amount is not None:
                present.add("amount_cents")
            if route.refund_reason is not None:
                # Only the reason is collected here. The refund type is derived
                # by the server from the order's refundable balance, so the
                # customer is never asked "full or partial".
                present.add("refund_reason")
            missing = catalog.missing_slots(plan.intent.value, present)

            request_text = (
                self._merged_request(carried, message) if resumed else message
            )

            user_message = conversations.record(
                turn.conversation,
                role="user",
                content=message,
                intent=plan.intent.value,
                # Replay only sees the transcript, so a context field the
                # client sent out of band must be kept with the message.
                payload={"order_id": order_id} if order_id else None,
            )

            self._detect_abandon(session, identity, turn, carried, plan, channel)
            # The write path runs in its own transaction — the runtime owns it.
            # Holding a write lock across that call would deadlock on SQLite
            # and block on PostgreSQL, so the turn's own writes go in first.
            session.commit()

            reply_started = time.perf_counter()
            takeover = decision is not None and decision.takeover
            counter_asked = False
            if takeover and decision is not None and not decision.continue_task:
                # The user is going to a human: the handover replaces the
                # answer instead of being appended to it.
                contexts = self._contexts(session)
                draft = self._dispatch(
                    session,
                    identity,
                    contexts,
                    plan,
                    state,
                    message=message,
                    request_text=request_text,
                    context=context,
                    trace=trace,
                    action_name="handoff.escalate",
                    signal=decision.signal,
                    model_decision=model_decision,
                )
            elif missing:
                draft = self._clarify(
                    plan,
                    missing[0],
                    amount_invalid=amount_invalid,
                    candidates=self._clarification_candidates(
                        session,
                        identity,
                        missing[0],
                        escalation_state.clarification_count,
                    ),
                )
            else:
                if counter_draft is not None and plan.intent is SupportIntent.UNKNOWN:
                    # The counter-question replaces only the UNKNOWN fallback
                    # menu (§3.3); every other landing keeps its own answer.
                    draft = counter_draft
                    counter_asked = True
                else:
                    contexts = self._contexts(session)
                    draft = self._dispatch(
                        session,
                        identity,
                        contexts,
                        plan,
                        state,
                        message=message,
                        request_text=request_text,
                        context=context,
                        trace=trace,
                        action_name="analytics.expand" if expand_query_id else None,
                        expand_query_id=expand_query_id,
                        model_decision=model_decision,
                    )

            # --- 阶段七：回合后再判一次，转人工与措辞都由信号表给 ---
            clarified = draft.clarification is not None
            escalation_state = self.escalation.count_outcome(
                intent=plan.intent.value,
                state=escalation_state,
                clarified=clarified,
                failed=not draft.resolved and not clarified,
            )
            post = self.escalation.evaluate_post(
                intent=plan.intent.value,
                state=escalation_state,
                evidence=draft.evidence,
            )
            if decision is None or (
                post is not None and post.takeover and not decision.takeover
            ):
                decision = post

            handoff_payload: dict[str, Any] | None = None
            if decision is not None:
                draft = self._shape(draft, decision)
                event = self._escalate(
                    session,
                    identity,
                    conversations,
                    turn.conversation,
                    plan,
                    draft,
                    decision,
                    escalation_state,
                    message,
                    trace,
                    handoffs,
                    channel,
                )
                handoff_payload = self._handoff_payload(event)
                trace.add(
                    "handoff",
                    "handoff.escalate",
                    attributes={
                        "signal_id": decision.signal_id,
                        "detail": decision.detail,
                        "reason": decision.signal.reason.value,
                        "queue": decision.signal.queue,
                        "priority": decision.signal.priority,
                        "status": event.status,
                        "event_id": event.id,
                        "takeover": decision.takeover,
                    },
                )

            # --- 阶段八：定稿——输出护栏是最后一道手，之后才写槽位与回复 ---
            outcome = self._outcome(draft, decision)
            personalization = self._personalize(
                session, identity, trace, draft, carried, state
            )
            self._guard_output(session, identity, trace, turn.conversation.id, draft)
            user_message.payload = {**user_message.payload, "outcome": outcome}

            pending_request: str | None = None
            if draft.clarification and catalog.get(plan.intent.value).write_path:
                # A rejected amount must not be carried into the resumed
                # request; otherwise the correction is joined with the old
                # value and the same validation fails again.
                pending_request = (
                    strip_amount(request_text)
                    if draft.clarification["reason"] == "INVALID_AMOUNT"
                    else request_text
                )

            # Whatever this turn writes back as the pending intent: the
            # clarification it just raised, or nothing. Extracted once because
            # the refund_reason lifetime below reads this same written-back
            # value (口径 §5.6.1.1), never the previous turn's
            # carried.pending_intent (that reading is vetoed (G)).
            pending_intent_value = (
                draft.clarification["resumable_intent"]
                if draft.clarification
                else None
            )
            # 存活域（口径 §5.6.1.1）：携带的原因只在本回合退款流程仍开着时
            # 保留 — 本回合规划/恢复为 REFUND_REQUEST，或本轮正要写回一个
            # REFUND_REQUEST 追问（条件读的是上面这个本轮写回值）。
            refund_flow_open = (
                plan.intent is SupportIntent.REFUND_REQUEST
                or pending_intent_value == SupportIntent.REFUND_REQUEST.value
            )

            slots = ConversationSlots(
                order_id=state.order_id,
                product_id=state.product_id,
                amount_cents=state.amount_cents,
                # Whatever decided this turn: the carried value or a fresh
                # match. None means the slot was never answered, not a
                # placeholder. A refund task created on this turn ends the
                # carry-over: the reason is the customer's choice made at the
                # start of one refund (口径 §1、§5.6.1.1 生命周期句), so the
                # next refund answers a fresh one instead of inheriting this
                # one; the carry also dies once the refund flow is no longer
                # open this turn, read against this turn's written-back
                # pending value (口径 §5.6.1.1). refund_type is not asked and
                # not cleared here.
                refund_reason=(
                    None
                    if not refund_flow_open
                    or (
                        draft.task is not None
                        and plan.intent is SupportIntent.REFUND_REQUEST
                    )
                    else (
                        state.refund_reason.value
                        if state.refund_reason is not None
                        else None
                    )
                ),
                pending_intent=pending_intent_value,
                pending_request=pending_request,
                failed_intents=escalation_state.failed_intents,
                clarification_count=escalation_state.clarification_count,
                negative_rounds=escalation_state.negative_rounds,
                # 单轮上限随本回合唯一一次槽位写回落库（best-effort，口径 §5.1）：
                # 只有反问文本真的产出了才置位，被拦的轮次不清 carried 位。
                counter_question_asked=carried.counter_question_asked or counter_asked,
                last_intent=plan.intent.value,
                last_analytics_query_id=self._next_analytics_query_id(
                    carried, draft, expand_query_id
                ),
                resolved=draft.resolved or bool(takeover),
                last_message_at=utcnow().isoformat(),
            )
            conversations.save_slots(turn.conversation, slots)
            conversations.record(
                turn.conversation,
                role="assistant",
                content=draft.reply,
                intent=draft.intent.value,
                payload={"outcome": outcome, "trace_id": trace.trace_id},
            )
            conversations.trim(turn.conversation)
            clarification = draft.clarification or {}
            trace.add(
                "generate",
                "support.reply",
                attributes={
                    "intent": draft.intent.value,
                    "reply_chars": len(draft.reply),
                    "reply_sha256": reply_digest(draft.reply),
                    "cards": len(draft.cards),
                    "clarified": draft.clarification is not None,
                    "clarify_reason": clarification.get("reason"),
                    "clarify_slot": clarification.get("expected_slot"),
                    # A count, never the order numbers: candidates are the
                    # customer's own rows and the ledger is read by other people.
                    "clarify_candidates": _clarify_candidate_count(draft),
                    "outcome": outcome,
                    "action": draft.action,
                },
                latency_ms=tracing.elapsed_ms(reply_started),
            )
            TraceRepository(session).flush(trace)
            session.commit()

            return {
                "conversation_id": state.conversation_id,
                "reply": draft.reply,
                "intent": draft.intent.value,
                "agent_mode": self.mode,
                "context_cards": draft.cards,
                "task": draft.task,
                "suggestions": draft.suggestions,
                "slots": slots.to_dict(),
                "inherited_slots": state.inherited,
                "clarification": draft.clarification,
                "trace_id": trace.trace_id,
                "agent_silent": False,
                "handoff": handoff_payload,
                "personalization": personalization,
            }

    # --------------------------------------------------------------- stages
    def _stage_read_slots(
        self,
        message: str,
        order_id: str | None,
        channel: Channel,
        carried: ConversationSlots,
    ) -> _SlotReading:
        """把这一句客户输入读成槽位，再让关键词表与意图目录各读一次意图。

        三个来源的优先级是固定的：本条消息 > 调用方显式传入 > 上一回合携带。
        读不出来的槽位留空交给追问，绝不用默认值顶上——把缺失猜成某个默认值，
        用户就再也纠正不了它。返回的 ``context`` 同时是路由、模型和任务上下文
        的输入，所以它只装读得出的值。
        """
        message_order = extract_order_id(message)
        message_amount, amount_invalid = extract_amount(message)
        api_order = order_id.upper() if order_id else None
        resolved_order = message_order or api_order or carried.order_id
        resolved_amount = (
            message_amount
            if message_amount is not None
            else (None if amount_invalid else carried.amount_cents)
        )

        context: dict[str, Any] = {}
        if resolved_order:
            context["order_id"] = resolved_order
        if resolved_amount is not None:
            context["amount_cents"] = resolved_amount
        carried_reason = _refund_reason(carried.refund_reason)
        if carried_reason is not None:
            # The customer already answered the reason in an earlier turn.
            # classify() reads the context key before matching this text,
            # so the carried value wins and the slot is never asked twice.
            # A dirty stored value is treated as "not carried": never None,
            # an empty string or any placeholder is written into context.
            context["refund_reason"] = carried_reason.value
        route = classify(message, context)
        # What the keyword table read out of this message, or what the
        # caller already supplied. A miss leaves the key out: the slot is
        # then asked for, never guessed.
        if route.refund_reason is not None:
            context["refund_reason"] = route.refund_reason.value
        if route.refund_type is not None:
            context["refund_type"] = route.refund_type.value
        # Added only after routing so the channel stays provenance: it lands in
        # the task context for sampling, never in a decision input.
        context["channel"] = channel.value
        matched_product = self.catalog.resolve(message)
        product = matched_product or (
            self.catalog.for_order(resolved_order) if resolved_order else None
        )
        plan = classify_support(
            message,
            route,
            has_catalog_match=matched_product is not None,
            message_order_id=message_order,
            order_id=resolved_order,
        )
        return _SlotReading(
            message_order=message_order,
            message_amount=message_amount,
            amount_invalid=amount_invalid,
            api_order=api_order,
            resolved_order=resolved_order,
            resolved_amount=resolved_amount,
            context=context,
            route=route,
            plan=plan,
            product=product,
        )

    def _stage_consult_model(
        self,
        session: Session,
        identity: Identity,
        message: str,
        reading: _SlotReading,
        carried: ConversationSlots,
        turn: ConversationTurn,
    ) -> _Consultation:
        """决定这一回合的最终读法：沿用未完成的追问、展开上一条统计，或问一次模型。

        规则读得出就到此为止；只有规则读不出、而消息又是动作型时才允许问模型，
        并且只采用「退款」这一种读法（理由见 ``_consult_model``）。模型的成本
        记账写在本回合自己的会话上：另开一条连接会在 SQLite 上等这把写锁。
        """
        plan = reading.plan
        route = reading.route
        context = reading.context
        plan, resumed = self._resume_pending(plan, carried, message)
        expand_query_id = self._detail_followup(message, carried, plan.intent)
        if expand_query_id is not None:
            # Opening the previous statistic is a lookup in the snapshot
            # store, not a new question: no slot is missing, and the
            # model has nothing to add.
            plan = SupportPlan(SupportIntent("SELF_ANALYTICS"), reading.resolved_order)
            resumed = False
            model_gap: dict[str, Any] | None = {
                "source": "rules",
                "intent": plan.intent.value,
                "adopted": False,
                "gate": "detail_followup",
                # 与 consult 档同形：这一轮没有问模型，所以是「没有改道」，
                # 不是「问过但没改」。
                "redirect": False,
                "redirect_rejected": False,
                "redirect_kind": "none",
            }
            model_trace = None
            model_decision = None
            counter_draft: Draft | None = None
        else:
            # The candidate kernel never queries the database (口径 §2.2): the
            # caller resolves the order status here, under the same scope as
            # OrderService.require_order. Unreadable means None — no
            # evidence, never an error and never a substituted default.
            order_state = self._scoped_order_state(
                session, identity, reading.resolved_order
            )
            plan, model_gap, model_trace, model_decision, counter_draft = (
                self._consult_model(
                    plan,
                    route,
                    message,
                    context,
                    carried=carried,
                    order_state=order_state,
                )
            )
        if model_trace is not None:
            # Written on the turn's own session, before the commit below:
            # a second connection would wait on this one's write lock on
            # SQLite. The row is part of the turn it paid for.
            LlmUsageService(session).record(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                conversation_id=turn.conversation.id,
                source="chat",
                planning_source="pydantic_ai",
                # A test double may not expose the label; the row still
                # records the call without inventing a model name.
                model_name=getattr(self.runtime.brain, "model_name", None),
                requests=model_trace.requests,
                retries=model_trace.retries,
                input_tokens=model_trace.input_tokens,
                output_tokens=model_trace.output_tokens,
                latency_ms=model_trace.latency_ms,
                status=model_trace.status,
            )

        resealed_keys: list[str] = []
        if (
            plan.intent is SupportIntent.REFUND_REQUEST
            and route.refund_reason is None
        ):
            # classify() runs the rule table only on a turn it reads as a
            # refund itself. A refund reached by ``_resume_pending`` or a
            # model adoption must still see this turn's wording: this is
            # how a reason answered into a clarification gets collected
            # (口径 §5.6.1.1 沿用适用域). A miss stays a miss (§5.6.2).
            matched_reason = match_refund_reason(message)
            if matched_reason is not None:
                route = replace(route, refund_reason=matched_reason)
                context["refund_reason"] = matched_reason.value
                if model_decision is not None:
                    # The reason key joins the same context dict the
                    # ledger will re-verify against. The table read it out
                    # of this turn's own text under this turn's own rules,
                    # so the record is re-sealed over the final context
                    # rather than costing a second model call: every other
                    # verified field (request, rule reads, ruleset, model
                    # name) is exactly what the entry already held.
                    model_decision = replace(
                        model_decision,
                        digest=request_digest(message, context),
                    )
                    # Which server-derived keys landed in the context after
                    # the call. The digest itself is not recorded: a trace
                    # is read by seats and sampled into eval files.
                    resealed_keys.append("refund_reason")
        return _Consultation(
            plan=plan,
            route=route,
            context=context,
            resumed=resumed,
            expand_query_id=expand_query_id,
            model_gap=model_gap,
            model_trace=model_trace,
            model_decision=model_decision,
            counter_draft=counter_draft,
            resealed_keys=resealed_keys,
        )

    def _stage_escalation_pre(
        self,
        carried: ConversationSlots,
        message: str,
        plan: SupportPlan,
        resolved_amount: int | None,
        catalog: IntentCatalog,
    ) -> tuple[EscalationState, EscalationDecision | None]:
        """回合开始前问一次信号表：这一句是不是就该转人工了。

        读的是上一回合留下的失败意图、追问次数、负向轮数与沉默时长；本回合
        只把它们推进一格，不落库——写回发生在回合末尾的槽位里。
        """
        escalation_state = EscalationState(
            failed_intents=dict(carried.failed_intents),
            clarification_count=carried.clarification_count,
            negative_rounds=carried.negative_rounds,
            idle_minutes=self._idle_minutes(carried.last_message_at),
            amount_cents=resolved_amount,
        )
        escalation_state = self.escalation.count_message(
            message.lower(), escalation_state
        )
        decision = self.escalation.evaluate_pre(
            text=message.lower(),
            intent=plan.intent.value,
            risk_level=catalog.risk_of(plan.intent.value),
            state=escalation_state,
        )
        return escalation_state, decision

    def get_card(
        self, identity: Identity, kind: str, card_id: str
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            return self._contexts(session).get_card(identity, kind, card_id)

    def get_section(
        self, identity: Identity, kind: str, card_id: str, section_id: str
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            return self._contexts(session).get_section(
                identity, kind, card_id, section_id
            )

    # ------------------------------------------------------------ guardrails
    def _guard_input(
        self,
        session: Session,
        identity: Identity,
        conversation_id: str,
        message: str,
    ) -> list[SecurityEvent]:
        """Record attempts, never act on them.

        Routing, eligibility and the approval path are decided elsewhere; a
        customer saying the policy allows a refund must still meet the same
        rules as any other request.
        """
        if not self.settings.security_events_enabled:
            return []
        findings = scan_input(message)
        if not findings:
            return []
        service = SecurityEventService(session)
        events: list[SecurityEvent] = []
        for finding in findings:
            events.append(
                service.record(
                    tenant_id=identity.tenant_id,
                    user_id=identity.user_id,
                    conversation_id=conversation_id,
                    category=finding.category,
                    severity=finding.severity,
                    action=finding.action,
                    rule_id=finding.rule_id,
                    source="input",
                    excerpt=finding.excerpt,
                    detail={**finding.details, "message_digest": digest(message)},
                )
            )
        return events

    def _guard_output(
        self,
        session: Session,
        identity: Identity,
        trace: TraceRecorder,
        conversation_id: str,
        draft: Draft,
    ) -> None:
        if draft.clarification is not None:
            return
        source = self._output_source(draft)
        guard_started = time.perf_counter()
        result = guard_output(draft.reply, grounded=source is not None)
        if not result.changed:
            return
        draft.reply = result.reply
        if self.settings.security_events_enabled:
            SecurityEventService(session).record(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                conversation_id=conversation_id,
                category=SecurityCategory.OUTPUT_GROUNDING,
                severity=SecuritySeverity.MEDIUM,
                action=SecurityAction.REDACTED,
                rule_id="output.unsupported_assertion",
                source="output",
                excerpt=", ".join(result.kinds),
                detail={
                    "source": source or "none",
                    "replacements": result.replaced,
                    "kinds": list(result.kinds),
                },
            )
        trace.add(
            "guard",
            "output.grounded",
            attributes={
                "source": source or "none",
                "replacements": result.replaced,
                "kinds": list(result.kinds),
            },
            latency_ms=tracing.elapsed_ms(guard_started),
        )

    def _output_source(self, draft: Draft) -> str | None:
        if draft.task is not None:
            return "task"
        if draft.cards:
            return "card"
        if draft.action in _TOOL_SOURCED_ACTIONS:
            return "tool"
        return None

    # -------------------------------------------------------- personalization
    def _personalize(
        self,
        session: Session,
        identity: Identity,
        trace: TraceRecorder,
        draft: Draft,
        carried: ConversationSlots,
        state: Turn,
    ) -> dict[str, Any] | None:
        """Let the customer's own profile reshape the reply — templates only.

        Runs after the answer and the escalation wording are settled and
        before the output guard, so the guard is still the last thing that
        touches the text. See ``app/agent/personalization.py`` for what is
        allowed to change and why no customer free text reaches the answer.
        """
        if not self.settings.memory_personalization_enabled:
            return None
        profile = load_profile(session, identity)
        if profile is None:
            return None
        personalize_started = time.perf_counter()
        result = personalize(
            draft,
            profile,
            self.catalog,
            first_turn=carried.last_intent is None,
            product_id=state.product_id,
        )
        if not result.applied:
            return None
        trace.add(
            "generate",
            "memory.personalize",
            attributes={
                "effects": list(result.effects),
                "fields": sorted(
                    {EFFECT_FIELDS[effect] for effect in result.effects}
                ),
                "matched_product_id": result.matched_product_id,
                "first_turn": carried.last_intent is None,
            },
            latency_ms=tracing.elapsed_ms(personalize_started),
        )
        return result.to_dict()

    # ------------------------------------------------------------- silence
    def _silent(
        self,
        session: Session,
        conversations: ConversationService,
        turn: ConversationTurn,
        identity: Identity,
        message: str,
        state: HandoffStatus,
        handoffs: HandoffService,
    ) -> dict[str, Any]:
        """A human owns the conversation: the agent records and stays quiet.

        The user message is still stored, so the seat sees what the customer
        said while the agent was silent.
        """
        plan = classify_support(
            message,
            classify(message, {}),
            has_catalog_match=self.catalog.resolve(message) is not None,
            message_order_id=extract_order_id(message),
        )
        conversations.record(
            turn.conversation,
            role="user",
            content=message,
            intent=plan.intent.value,
            payload={"outcome": self._outcome(None, None, silent=True)},
        )
        trace = TraceRecorder(
            tenant_id=identity.tenant_id,
            conversation_id=turn.conversation.id,
            root_name="support.turn",
        )
        trace.add(
            "handoff",
            "agent.silent",
            attributes={
                "handoff_status": state.value,
                "intent": plan.intent.value,
                "reply_chars": 0,
            },
        )
        TraceRepository(session).flush(trace)
        session.commit()
        event = handoffs.active_event(turn.conversation.id)
        return {
            "conversation_id": turn.conversation.id,
            "reply": "",
            "intent": plan.intent.value,
            "agent_mode": self.mode,
            "context_cards": [],
            "task": None,
            "suggestions": [],
            "slots": turn.slots.to_dict(),
            "inherited_slots": [],
            "clarification": None,
            "trace_id": trace.trace_id,
            "agent_silent": True,
            "handoff": self._handoff_payload(event),
            "personalization": None,
        }

    # ------------------------------------------------------------ dispatch
    def _dispatch(
        self,
        session: Session,
        identity: Identity,
        contexts: SupportContextService,
        plan: SupportPlan,
        state: Turn,
        *,
        message: str,
        request_text: str,
        context: dict[str, Any],
        trace: TraceRecorder,
        action_name: str | None = None,
        signal: EscalationSignal | None = None,
        model_decision: ModelDecision | None = None,
        expand_query_id: str | None = None,
    ) -> Draft:
        """Hand the turn to the action the catalog binds to this intent."""
        action_names = get_catalog().actions_of(plan.intent.value)
        name = action_name or (action_names[0] if action_names else "none")
        handler = actions.REGISTRY.get(name)
        if handler is None:
            name = "none"
            handler = actions.REGISTRY["none"]
        draft = handler(
            self._request(
                session,
                identity,
                contexts,
                plan,
                state,
                message,
                request_text,
                context,
                trace,
                signal,
                model_decision,
                expand_query_id,
            )
        )
        draft.action = name
        return draft

    def _request(
        self,
        session: Session,
        identity: Identity,
        contexts: SupportContextService,
        plan: SupportPlan,
        state: Turn,
        message: str,
        request_text: str,
        context: dict[str, Any],
        trace: TraceRecorder,
        signal: EscalationSignal | None = None,
        model_decision: ModelDecision | None = None,
        expand_query_id: str | None = None,
    ) -> ActionRequest:
        return ActionRequest(
            session=session,
            identity=identity,
            contexts=contexts,
            catalog=self.catalog,
            policy=self.policy,
            runtime=self.runtime,
            analytics=self.analytics,
            plan=plan,
            state=state,
            message=message,
            request_text=request_text,
            context=context,
            trace=trace,
            signal=signal,
            model_decision=model_decision,
            narrator=self.narrator,
            conversation_id=state.conversation_id,
            expand_query_id=expand_query_id,
        )

    # ----------------------------------------------------------- clarifiers
    def _clarify(
        self,
        plan: SupportPlan,
        slot: str,
        *,
        amount_invalid: bool = False,
        candidates: list[dict[str, Any]] | None = None,
    ) -> Draft:
        catalog = get_catalog()
        if slot == "amount_cents" and amount_invalid:
            prompt = catalog.invalid_prompt_for(plan.intent.value, slot)
            reason = "INVALID_AMOUNT"
        else:
            prompt = catalog.prompt_for(plan.intent.value, slot)
            reason = catalog.missing_reason_for(slot)
        cards = list(candidates or [])
        if cards:
            ids = "、".join(str(card["card_id"]) for card in cards)
            reply = (
                f"{prompt}你名下最近的订单是 {ids}，"
                "点一下订单号我就接着处理；也可以直接把订单号发给我。"
            )
        elif slot != "order_id" and plan.order_id:
            reply = f"{prompt}订单号 {plan.order_id} 我已经记住了。"
        else:
            reply = f"{prompt}{_HANDOFF_TAIL}"
        return Draft(
            intent=plan.intent,
            reply=reply,
            cards=cards,
            suggestions=[
                *(str(card["card_id"]) for card in cards),
                "查询订单",
                "退款条件是什么？",
                "转人工",
            ],
            clarification={
                "reason": reason,
                "prompt": prompt,
                "expected_slot": slot,
                "resumable_intent": plan.intent.value,
            },
            resolved=False,
        )

    def _clarification_candidates(
        self,
        session: Session,
        identity: Identity,
        slot: str,
        clarification_count: int,
    ) -> list[dict[str, Any]]:
        """The second asking's candidate list, from the caller's own orders.

        Only the repeat is downgraded: the first asking stays the plain slot
        question, and a missing amount gets no order list. Candidates are never
        guessed or matched fuzzily — they are rows this caller already owns.
        """
        if slot != "order_id" or clarification_count < 1:
            return []
        orders = CommerceRepository(session).list_recent_orders(
            identity.tenant_id, identity.user_id, limit=CANDIDATE_LIMIT
        )
        return [
            order_candidate(order, self.catalog.for_order(order.id)) for order in orders
        ]

    # ---------------------------------------------------------- escalation
    def _shape(self, draft: Draft, decision: EscalationDecision) -> Draft:
        """Put the handover wording next to the answer the table declares."""
        signal = decision.signal
        if signal.reply_mode == "replace" and not decision.continue_task:
            draft.reply = signal.reply
        elif signal.reply:
            draft.reply = f"{draft.reply} {signal.reply}".strip()
        return draft

    def _escalate(
        self,
        session: Session,
        identity: Identity,
        conversations: ConversationService,
        conversation: Conversation,
        plan: SupportPlan,
        draft: Draft,
        decision: EscalationDecision,
        escalation_state: EscalationState,
        message: str,
        trace: TraceRecorder,
        handoffs: HandoffService,
        channel: Channel,
    ) -> HandoffEvent:
        """Raise the escalation and hand the context package to the seat."""
        pending_task_id = None
        if draft.task:
            pending_task_id = str(draft.task.get("task_id") or "") or None
        bundle = build_bundle(
            identity=identity,
            conversation=conversation,
            signal=decision.signal,
            reason_detail=decision.detail,
            messages=conversations.history(conversation, limit=20),
            spans=[
                {
                    "kind": span.kind,
                    "name": span.name,
                    "status": span.status,
                    "latency_ms": span.latency_ms,
                }
                for span in trace.spans
            ],
            slots=self._bundle_slots(conversation, escalation_state),
            pending_task_id=pending_task_id,
            sentiment=self.escalation.sentiment(
                message.lower(), escalation_state
            ),
            policy_refs=self._citations(draft),
            attempted_actions=self._attempted(draft),
            attempted_candidates=self._attempted_candidates(draft),
            digest_max_chars=self.policy.escalation.digest_max_chars,
            channel=channel,
        )
        return handoffs.escalate(
            identity,
            conversation=conversation,
            signal=decision.signal,
            bundle=bundle,
            intent=plan.intent.value,
            detail=decision.detail,
            trace_id=trace.trace_id,
            pending_task_id=pending_task_id,
        )

    def _bundle_slots(
        self, conversation: Conversation, escalation_state: EscalationState
    ) -> dict[str, Any]:
        slots = dict(conversation.slots or {})
        slots["failed_intents"] = dict(escalation_state.failed_intents)
        slots["clarification_count"] = escalation_state.clarification_count
        slots["negative_rounds"] = escalation_state.negative_rounds
        return slots

    def _citations(self, draft: Draft) -> list[Citation]:
        citations: list[Citation] = []
        first = True
        for card in draft.cards:
            if card.get("kind") != "policy":
                continue
            fields = card.get("fields") or {}
            citations.append(
                Citation(
                    document_id=str(card.get("card_id", "")),
                    title=str(card.get("title", "")),
                    version=str(fields.get("version", "")),
                    section=fields.get("matched_section"),
                    source_uri=fields.get("source_uri"),
                    score=draft.evidence.score
                    if first and draft.evidence is not None
                    else None,
                )
            )
            first = False
        return citations

    def _attempted(self, draft: Draft) -> list[ActionRecord]:
        if not draft.action:
            return []
        return [
            ActionRecord(
                action=draft.action,
                intent=draft.intent.value,
                result=self._outcome(draft, None),
                ok=draft.resolved,
                at=utcnow().isoformat(),
            )
        ]

    def _attempted_candidates(self, draft: Draft) -> list[CandidateOrder]:
        """The list this customer was already shown, so the seat starts there.

        Read back from the draft's own cards: what the seat is told was offered
        is exactly what the customer was rendered, never a fresh query that may
        have moved on since.
        """
        candidates: list[CandidateOrder] = []
        for card in draft.cards:
            if card.get("kind") != "order_candidate":
                continue
            fields = card.get("fields") or {}
            candidates.append(
                CandidateOrder(
                    order_id=str(card.get("card_id", "")),
                    product_name=str(fields.get("product_name") or ""),
                    status=str(fields.get("status") or ""),
                    delivered_at=(
                        str(fields["delivered_at"])
                        if fields.get("delivered_at") is not None
                        else None
                    ),
                )
            )
        return candidates

    def _outcome(
        self,
        draft: Draft | None,
        decision: EscalationDecision | None,
        *,
        silent: bool = False,
    ) -> Outcome:
        if silent:
            return Outcome.AGENT_SILENT
        if draft is None:
            raise ValueError("_outcome needs a draft unless silent=True")
        if decision is not None and decision.takeover:
            return Outcome.HANDOFF
        if draft.clarification is not None:
            return Outcome.CLARIFIED
        if not draft.resolved:
            return Outcome.UNRESOLVED
        return Outcome.ANSWERED

    def _handoff_payload(self, event: HandoffEvent | None) -> dict[str, Any] | None:
        if event is None:
            return None
        return {
            "event_id": event.id,
            "status": event.status,
            "queue": event.queue,
            "priority": event.priority,
            "reason": event.reason,
            "signal_id": event.signal_id,
            "takeover": event.status
            in {
                HandoffStatus.HANDOFF_PENDING.value,
                HandoffStatus.HUMAN_ACTIVE.value,
                HandoffStatus.QUEUED.value,
            },
        }

    def _detect_abandon(
        self,
        session: Session,
        identity: Identity,
        turn: ConversationTurn,
        carried: ConversationSlots,
        plan: SupportPlan,
        channel: Channel,
    ) -> None:
        """Record the user coming back to a question that was never answered."""
        abandon = AbandonService(session, policy=self.policy)
        idle = self._idle_minutes(carried.last_message_at)
        if (
            carried.last_message_at
            and not carried.resolved
            and idle >= self.policy.escalation.abandon_idle_minutes
        ):
            abandon.record(
                identity,
                conversation_id=turn.conversation.id,
                intent=carried.last_intent,
                reason=AbandonReason.IDLE_NO_RESOLUTION,
                idle_minutes=idle,
                detail={"last_intent": carried.last_intent},
                channel=channel,
            )
        abandon.detect_reentry(
            identity,
            intent=plan.intent.value,
            exclude_conversation_id=turn.conversation.id,
            channel=channel,
        )

    # ------------------------------------------------------------- helpers
    def _consult_model(
        self,
        plan: SupportPlan,
        route: RoutePlan,
        message: str,
        context: dict[str, Any],
        *,
        carried: ConversationSlots,
        order_state: str | None = None,
    ) -> tuple[
        SupportPlan,
        dict[str, Any] | None,
        ModelTrace | None,
        ModelDecision | None,
        Draft | None,
    ]:
        """The one place a chat turn may reach the model, and what it may buy.

        The intent catalog decides first. A turn is a gap when the rule table
        reads it as ``UNKNOWN`` *and* the catalog has no cue for it; the row
        that only fires because the message carried an order id
        (``ORDER_QUERY``) is not a reading, so it counts as a gap too —
        "ORD-... 那笔钱给我退回来" should not be answered with an order card
        just because of the id.

        Only an ``ADOPTABLE_INTENTS`` reading is adopted — today that set is the
        refund intent alone, and it is the same set the task runtime's
        ``IntentBrain.plan`` reads, so the two entries cannot drift apart
        (口径 ``.qoder/rules/adoption-surface.md``). That is deliberate:

        * the refund it opens still has to pass eligibility and a human
          approval before any money moves, so the model changes *whether a
          request enters the queue*, never whether money is paid;
        * every other model reading (policy, logistics, ticket) is dropped:
          answering from a wrong reading or creating a ticket on one is not
          worth the sentence it would rescue yet;
        * the amount is not adopted here. A number the customer did not type
          must not reach a clarification prompt; it is only used by the task
          runtime, where eligibility and the approval record show it again.

        ``AGENT_MODE=rules`` returns before the model is touched, so the
        deterministic path is unchanged. A model failure comes back as
        ``rules_fallback`` and leaves the plan as the catalog read it.

        The returned dict is the trace record of the decision, not a control
        signal: it lands in the ``understand`` span so a replay can show that
        the turn was asked, what came back, and whether it was adopted. The
        third value is the cost record of that call (``None`` when the model
        was not asked); the caller writes it to the usage ledger on the
        turn's own transaction. The fifth value is the one counter-question
        the question gate may buy (F-1, ``.qoder/rules/clarify-counter-question.md``
        §3/§6.1): only ever set when a question actually formed, and the
        question branch never calls ``plan`` — a probe reading must not
        reach the ledger-reuse branch, which would adopt it de facto.

        Wiring (``.qoder/rules/intent-candidates.md`` §2.2): the candidate
        kernel takes ``context:`` evidence from the ``pending_intent`` /
        ``last_intent`` keys and ``order:`` evidence from a caller-supplied
        ``order_state``. This entry therefore derives a *model-only* dict
        carrying the two slot keys (``None`` when a slot is empty — the
        kernel fabricates nothing from a non-string), and passes the resolved
        ``order_state`` through. The ledger ``context`` and the
        ``ModelDecision`` digest over it stay byte-identical (§2.3 rule 6).
        """
        decision = model_gate(plan, route, message)
        if decision == "rules_hit":
            return plan, None, None, None, None
        if self.settings.agent_mode != "pydantic_ai":
            return plan, None, None, None, None
        # Model-only derivation: the two slot keys come from the carried
        # conversation state, verbatim field names (口径 §2.2). A missing slot
        # is None, and the kernel reads None as "no such evidence" — it is
        # never a placeholder that could fake an inclusion. Derived once
        # because both model branches (the question probe and the consult
        # call) hand the kernel the same evidence.
        model_context = dict(context)
        model_context["pending_intent"] = carried.pending_intent
        model_context["last_intent"] = carried.last_intent
        if decision == "question":
            if carried.counter_question_asked:
                # 上限已用：不调模型，今天的 gap 逐字返回。顺序回合内尽力
                # （best-effort），并发不设上界（口径 §5.1）。
                return plan, dict(_QUESTION_GAP), None, None, None
            if plan.intent is not SupportIntent.UNKNOWN:
                # 入口守卫：反问只替代 UNKNOWN 兜底菜单（§3.3），规则读数非
                # UNKNOWN 的这一轮（例如带订单号被读成 ORDER_QUERY、却因第二门
                # 进入问句支的回合）按构造不可能展示给客户，付它没有任何可观察
                # 收益。不调模型、今天 gap 逐字返回、零成本、不产 ModelDecision、
                # 不写状态位。:537 的消费处检查保留，作第二道保险。
                return plan, dict(_QUESTION_GAP), None, None, None
            probe = self.runtime.brain.probe_question(
                message, model_context, order_state=order_state
            )
            if not probe.asks or probe.trace is None:
                # 没成反问就不改 gap 的键集（§3.2），但这次调用已经付了钱，
                # 成本行照落（§13.3）。``probe.trace is None`` 只在 rules 模式
                # 探针出现，本分支实际不可达，仍然如实返回 None 不伪造成本行。
                return plan, dict(_QUESTION_GAP), probe.trace, None, None
            draft = _counter_question_draft(probe, plan)
            if draft is None:
                # 标签表凑不出完整问句：不反问、gap 照旧，但调用已发生，
                # 成本经既有用量台账照常可见（§4.7 不静默吞）。
                return plan, dict(_QUESTION_GAP), probe.trace, None, None
            question_gap = dict(_QUESTION_GAP)
            # The only two changes §2.2 allows when a question formed: the
            # real reading source, and the cost record carried — not
            # recomputed — onto the existing ``understand`` span.
            question_gap["source"] = probe.source
            question_gap["model"] = probe.trace.as_dict()
            return plan, question_gap, probe.trace, None, draft
        routed, source, model_trace = self.runtime.brain.plan(
            message, model_context, order_state=order_state
        )
        gap: dict[str, Any] = {
            "source": source,
            "intent": routed.intent.value,
            "adopted": False,
            "gate": "consult",
            # 改道计数：`redirect` 是被采纳的改道，`redirect_rejected` 是模型
            # 想改而被采纳面拒掉的读数。两者是不同的数，不得相加减；真正的
            # 「错判」需要 gold 标签，只在离线评测里算（口径
            # `.qoder/rules/adoption-surface.md` §2.3）。默认值 = 这一轮没有
            # 记录过判定（模型没被问、或调用失败），不是「判过且为假」。
            "redirect": False,
            "redirect_rejected": False,
            "redirect_kind": "none",
        }
        record: ModelDecision | None = None
        if model_trace is not None:
            gap["model"] = model_trace.as_dict()
            # 搬运而不是重算：只有 brain 见过模型的原始回答，它把方向分类好
            # 写进 trace，这里照抄（与 `model_reason` 的复用纪律同形）。
            if model_trace.redirect is not None:
                gap["redirect"] = model_trace.redirect
                gap["redirect_rejected"] = bool(model_trace.redirect_rejected)
                gap["redirect_kind"] = model_trace.redirect_kind or "none"
        if source == "pydantic_ai" and model_trace is not None:
            # The ledger may reuse this reading, but only after checking it
            # against its own request, rule set and model configuration.
            record = ModelDecision(
                digest=request_digest(message, context),
                ruleset=RULESET_FINGERPRINT,
                model_name=getattr(self.runtime.brain, "model_name", None),
                rule_intent=route.intent.value,
                rule_order_id=route.order_id,
                rule_amount_cents=route.amount_cents,
                model_intent=routed.intent.value,
                model_order_id=routed.order_id,
                model_amount_cents=routed.amount_cents,
                trace=model_trace.as_dict(),
            )
        if source != "pydantic_ai" or routed.intent not in ADOPTABLE_INTENTS:
            return plan, gap, model_trace, record, None
        gap["adopted"] = True
        return (
            SupportPlan(
                SupportIntent(Intent.REFUND_REQUEST.value),
                routed.order_id or plan.order_id,
            ),
            gap,
            model_trace,
            record,
            None,
        )

    def _scoped_order_state(
        self,
        session: Session,
        identity: Identity,
        order_id: str | None,
    ) -> str | None:
        """The current order status for candidate evidence, or ``None``.

        Same scope as ``OrderService.require_order`` (tenant equal; user equal
        unless the caller holds the support role), but it never raises: a
        missing, foreign or unreadable order simply means there is no
        ``order:`` evidence. The candidate kernel must not query the database
        (``.qoder/rules/intent-candidates.md`` §2.2), so the caller resolves
        it here — and every lookup keeps the tenant boundary.
        """
        if not order_id:
            return None
        order = CommerceRepository(session).get_order(order_id)
        if order is None or order.tenant_id != identity.tenant_id:
            return None
        if order.user_id != identity.user_id and not identity.has_role("support"):
            return None
        return order.status.value

    def _resume_pending(
        self, plan: SupportPlan, carried: ConversationSlots, message: str
    ) -> tuple[SupportPlan, bool]:
        pending = carried.pending_intent
        if not pending:
            return plan, False
        try:
            pending_intent = SupportIntent(pending)
        except ValueError:
            return plan, False
        if pending_intent.value not in get_catalog().order_scoped_ids():
            return plan, False
        if plan.intent is SupportIntent.UNKNOWN or (
            plan.intent is SupportIntent.ORDER_QUERY
            and self._is_bare_order_reply(message)
        ):
            return SupportPlan(pending_intent, plan.order_id), True
        return plan, False

    def _detail_followup(
        self, message: str, carried: ConversationSlots, intent: SupportIntent
    ) -> str | None:
        """The open snapshot this message asks to see, if it asks for one.

        A message that names a record set is a new statistic, not a follow-up
        ("订单明细" must not open last week's refund rows); a message with no
        open snapshot has nothing to open. Only an analytics-shaped turn may
        open one: a policy question that happens to say "明细" must stay a
        policy question, or the customer gets last week's rows instead.
        """
        if not carried.last_analytics_query_id:
            return None
        if intent is not SupportIntent.UNKNOWN and intent.value != "SELF_ANALYTICS":
            return None
        if names_dataset(message) is not None:
            return None
        if not asks_for_detail_followup(message):
            return None
        return carried.last_analytics_query_id

    def _next_analytics_query_id(
        self,
        carried: ConversationSlots,
        draft: Draft,
        expand_query_id: str | None,
    ) -> str | None:
        """Keep the open snapshot correct across one more turn.

        A new statistic replaces it; a successful expansion keeps the same
        id; a failed expansion closes it so the next question cannot chase a
        snapshot that is already gone.
        """
        if expand_query_id is not None:
            return draft.analytics_query_id
        if draft.action == "analytics.query":
            # Every statistics turn supersedes the open snapshot. The SQL
            # route has no snapshot to open, so the slot must close rather
            # than keep pointing at an older, different statistic.
            return draft.analytics_query_id
        return carried.last_analytics_query_id

    def _merged_request(self, carried: ConversationSlots, message: str) -> str:
        parts = [
            part
            for part in (carried.pending_request, strip_amount(message))
            if part
        ]
        return " ".join(parts)

    def _is_bare_order_reply(self, message: str) -> bool:
        remainder = extract_order_id(message)
        stripped = message.upper().replace(remainder or "\0", " ")
        text = stripped.lower()
        catalog = get_catalog()
        return not any(
            cue in text for spec in catalog.ordered_specs() for cue in spec.cues
        )

    def _inherited_slots(
        self,
        carried: ConversationSlots,
        *,
        message_order: str | None,
        api_order: str | None,
        message_amount: int | None,
        resolved_order: str | None,
        resolved_amount: int | None,
    ) -> list[str]:
        inherited: list[str] = []
        if (
            carried.order_id
            and resolved_order == carried.order_id
            and not message_order
            and not api_order
        ):
            inherited.append("order_id")
        if (
            carried.amount_cents is not None
            and resolved_amount == carried.amount_cents
            and message_amount is None
        ):
            inherited.append("amount_cents")
        return inherited

    def _idle_minutes(self, last_message_at: str | None) -> int:
        if not last_message_at:
            return 0
        try:
            moment = datetime.fromisoformat(last_message_at)
        except ValueError:
            return 0
        return max(0, int((utcnow() - moment).total_seconds() // 60))

    def _contexts(self, session: Session) -> SupportContextService:
        results = max(self.settings.policy_max_results, 6)
        return SupportContextService(
            session,
            self.catalog,
            engine=self.policy,
            policy_max_results=results,
            policy_min_score=self.settings.policy_min_score,
            policy_search=build_policy_search(
                session,
                self.settings,
                max_results=results,
                min_score=self.settings.policy_min_score,
            ),
        )
