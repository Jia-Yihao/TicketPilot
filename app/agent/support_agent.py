from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session, sessionmaker

from app.agent import actions
from app.agent.actions import ActionRequest
from app.agent.escalation import EscalationSignal, get_escalation_catalog
from app.agent.handoff import ActionRecord, Citation
from app.agent.intent_catalog import get_catalog
from app.agent.personalization import (
    EFFECT_FIELDS,
    load_profile,
    personalize,
)
from app.agent.routing import (
    RoutePlan,
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
from app.models.enums import AbandonReason, Channel, HandoffStatus, Intent
from app.models.handoff import HandoffEvent
from app.models.security_event import SecurityEvent
from app.observability.redaction import digest
from app.observability.tracing import TraceRecorder, reply_digest
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
from app.services.support import SupportContextService
from app.sql.service import SqlAnalyticsService

if TYPE_CHECKING:
    from app.agent.pydantic_agent import ModelTrace

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
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.runtime = runtime
        self.catalog = catalog or CatalogService()
        self.policy = policy or PolicyEngine.from_settings(settings)
        self.analytics = analytics or SqlAnalyticsService(
            max_limit=settings.analytics_max_limit
        )
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

            route = classify(message, context)
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
            plan, resumed = self._resume_pending(plan, carried, message)
            plan, model_gap, model_trace = self._consult_model(
                plan,
                route,
                message,
                context,
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

            catalog = get_catalog()
            trace = TraceRecorder(
                tenant_id=identity.tenant_id,
                conversation_id=turn.conversation.id,
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
                    "product_id": product.product_id if product else None,
                    "risk_level": catalog.risk_of(plan.intent.value).value,
                    "catalog_version": catalog.version,
                    "guard_categories": [event.category for event in guard_events],
                },
            )

            state = Turn(
                conversation_id=turn.conversation.id,
                order_id=resolved_order,
                amount_cents=resolved_amount,
                product_id=product.product_id if product else carried.product_id,
                inherited=self._inherited_slots(
                    carried,
                    message_order=message_order,
                    api_order=api_order,
                    message_amount=message_amount,
                    resolved_order=resolved_order,
                    resolved_amount=resolved_amount,
                ),
            )

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

            present: set[str] = set()
            if resolved_order:
                present.add("order_id")
            if resolved_amount is not None:
                present.add("amount_cents")
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

            takeover = decision is not None and decision.takeover
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
                )
            elif missing:
                draft = self._clarify(plan, missing[0], amount_invalid=amount_invalid)
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
                )

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
                evidence_score=draft.evidence_score,
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

            slots = ConversationSlots(
                order_id=state.order_id,
                product_id=state.product_id,
                amount_cents=state.amount_cents,
                pending_intent=(
                    draft.clarification["resumable_intent"]
                    if draft.clarification
                    else None
                ),
                pending_request=pending_request,
                failed_intents=escalation_state.failed_intents,
                clarification_count=escalation_state.clarification_count,
                negative_rounds=escalation_state.negative_rounds,
                last_intent=plan.intent.value,
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
                    "outcome": outcome,
                    "action": draft.action,
                },
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
            payload={"outcome": "AGENT_SILENT"},
        )
        trace = TraceRecorder(
            tenant_id=identity.tenant_id,
            conversation_id=turn.conversation.id,
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
        )

    # ----------------------------------------------------------- clarifiers
    def _clarify(
        self, plan: SupportPlan, slot: str, *, amount_invalid: bool = False
    ) -> Draft:
        catalog = get_catalog()
        if slot == "amount_cents" and amount_invalid:
            prompt = catalog.invalid_prompt_for(plan.intent.value, slot)
            reason = "INVALID_AMOUNT"
        else:
            prompt = catalog.prompt_for(plan.intent.value, slot)
            reason = catalog.missing_reason_for(slot)
        reply = f"{prompt}你也可以直接说「转人工」，我会转给人工同事接手。"
        if slot != "order_id" and plan.order_id:
            reply = f"{prompt}订单号 {plan.order_id} 我已经记住了。"
        return Draft(
            intent=plan.intent,
            reply=reply,
            suggestions=["查询订单", "退款条件是什么？", "转人工"],
            clarification={
                "reason": reason,
                "prompt": prompt,
                "expected_slot": slot,
                "resumable_intent": plan.intent.value,
            },
            resolved=False,
        )

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
                    score=draft.evidence_score if first else None,
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

    def _outcome(self, draft: Draft, decision: EscalationDecision | None) -> str:
        if decision is not None and decision.takeover:
            return "HANDOFF"
        if draft.clarification is not None:
            return "CLARIFIED"
        if not draft.resolved:
            return "UNRESOLVED"
        return "ANSWERED"

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
    ) -> tuple[SupportPlan, dict[str, Any] | None, ModelTrace | None]:
        """The one place a chat turn may reach the model, and what it may buy.

        The intent catalog decides first. A turn is a gap when the rule table
        reads it as ``UNKNOWN`` *and* the catalog has no cue for it; the row
        that only fires because the message carried an order id
        (``ORDER_QUERY``) is not a reading, so it counts as a gap too —
        "ORD-... 那笔钱给我退回来" should not be answered with an order card
        just because of the id.

        Only a *refund* reading is adopted. That is deliberate:

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
        turn's own transaction.
        """
        decision = model_gate(plan, route, message)
        if decision == "rules_hit":
            return plan, None, None
        if self.settings.agent_mode != "pydantic_ai":
            return plan, None, None
        if decision == "question":
            return (
                plan,
                {
                    "source": "rules",
                    "intent": "UNKNOWN",
                    "adopted": False,
                    "gate": "question",
                },
                None,
            )
        routed, source, model_trace = self.runtime.brain.plan(message, context)
        gap: dict[str, Any] = {
            "source": source,
            "intent": routed.intent.value,
            "adopted": False,
            "gate": "consult",
        }
        if model_trace is not None:
            gap["model"] = model_trace.as_dict()
        if source != "pydantic_ai" or routed.intent is not Intent.REFUND_REQUEST:
            return plan, gap, model_trace
        gap["adopted"] = True
        return (
            SupportPlan(
                SupportIntent(Intent.REFUND_REQUEST.value),
                routed.order_id or plan.order_id,
            ),
            gap,
            model_trace,
        )

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
