from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.agent.pydantic_agent import IntentBrain
from app.agent.routing import RoutePlan
from app.channels.outbound import PROACTIVE_EVENT_TYPE, ProactiveSender
from app.core.config import Settings
from app.core.errors import (
    AppError,
    ApprovalRequiredError,
    ClarificationRequired,
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    NotFoundError,
    ValidationAppError,
)
from app.core.money import format_cents
from app.core.policy import PolicyEngine
from app.core.security import Identity, require_approver
from app.models.approval import Approval
from app.models.commerce import Order
from app.models.enums import (
    ApprovalStatus,
    Intent,
    TaskStatus,
    VerificationStatus,
)
from app.models.outbox import OutboxMessage
from app.models.task import Task
from app.observability.tracing import TraceRecorder
from app.repositories.approvals import ApprovalRepository
from app.repositories.audit import AuditRepository
from app.repositories.tasks import TaskRepository
from app.repositories.traces import TraceRepository
from app.retrieval.build import build_policy_search
from app.services.approvals import ApprovalService, canonical_digest, refund_payload
from app.services.executions import ExecutionService
from app.services.llm_usage import LlmUsageService
from app.services.orders import OrderService
from app.services.outbox import (
    DeliveryReport,
    MessageNotDelivered,
    OutboxService,
    OutboxSink,
)
from app.services.refund_policy import RefundPolicy
from app.services.refunds import RefundExecutionService, RefundOutcome
from app.services.tickets import TicketService
from app.tools.base import DegradationStep, ToolContext, ToolResult
from app.tools.builtin import build_registry
from app.tools.executor import CircuitBreaker, ToolExecutionError, ToolExecutor
from app.tools.gateway import ToolGateway

logger = logging.getLogger(__name__)

TERMINAL = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.TIMED_OUT,
    TaskStatus.REJECTED,
    TaskStatus.EXPIRED,
}

REFUND_PENDING_CONFIRMATION = "refund.pending_confirmation"


def _degradation_step(
    steps: tuple[DegradationStep, ...], exc: AppError
) -> DegradationStep | None:
    """Pick the degradation step that matches the failure form.

    An executor failure already carries the step it chose; only the failures
    that happened before the call (a rejected argument) are matched here.
    """
    if isinstance(exc, ToolExecutionError):
        return exc.degradation
    if isinstance(exc, ValidationAppError):
        wanted = "ask_user"
    elif isinstance(exc, (DependencyUnavailableError, TimeoutError)):
        wanted = "retry_later"
    elif isinstance(exc, (ApprovalRequiredError, ConflictError)):
        wanted = "stop"
    else:
        wanted = "handoff"
    for step in steps:
        if step.kind == wanted:
            return step
    return None


@dataclass(slots=True)
class RuntimeServices:
    tasks: TaskRepository
    approvals: ApprovalRepository
    executions: ExecutionService
    approval_service: ApprovalService
    orders: OrderService
    policy: RefundPolicy
    tickets: TicketService
    refunds: RefundExecutionService
    gateway: ToolGateway
    outbox: OutboxService


class TaskRuntime:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        brain: IntentBrain | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.brain = brain or IntentBrain(mode="rules")
        self.policy = PolicyEngine.from_settings(settings)
        # One breaker per process: it is shared by every request, because
        # the point of a breaker is to notice that the upstream is broken
        # for everyone, not for one session.
        self.executor = ToolExecutor(breaker=CircuitBreaker())

    def _services(self, session: Session) -> RuntimeServices:
        orders = OrderService(session)
        policy = RefundPolicy(session, self.policy)
        policies = build_policy_search(
            session,
            self.settings,
            max_results=self.settings.policy_max_results,
            min_score=self.settings.policy_min_score,
        )
        tickets = TicketService(session)
        refunds = RefundExecutionService(
            session,
            mode=self.settings.mock_refund_mode,
            delay_seconds=self.settings.mock_refund_delay_seconds,
            approval_ttl_minutes=self.settings.approval_ttl_minutes,
            engine=self.policy,
        )
        registry = build_registry(
            orders=orders, policies=policies, tickets=tickets, refunds=refunds, policy=policy
        )
        return RuntimeServices(
            tasks=TaskRepository(session),
            approvals=ApprovalRepository(session),
            executions=ExecutionService(session),
            approval_service=ApprovalService(
                session, ttl_minutes=self.settings.approval_ttl_minutes
            ),
            orders=orders,
            policy=policy,
            tickets=tickets,
            refunds=refunds,
            gateway=ToolGateway(session, registry, executor=self.executor),
            outbox=OutboxService(session),
        )

    def create_task(
        self,
        identity: Identity,
        request: str,
        context: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create and run a task, optionally under a client retry key.

        Same key + same request digest returns the first task and never runs
        it twice. Same key + a different request is a conflict, because
        replaying the old result would answer a question the client did not
        ask. The unique constraint is the arbiter under a race; the lookup
        here only saves the common sequential retry a write.
        """
        key = (idempotency_key or "").strip() or None
        digest = (
            canonical_digest({"request": request, "context": context or {}})
            if key
            else None
        )
        if key is not None:
            with self.session_factory() as session:
                existing = TaskRepository(session).find_by_idempotency(
                    identity.tenant_id, identity.user_id, key
                )
                if existing is not None:
                    existing_id = existing.id
                    existing_digest = existing.request_digest
                else:
                    existing_id = None
                    existing_digest = None
            if existing_id is not None:
                if existing_digest != digest:
                    raise ConflictError(
                        "Idempotency-Key was reused with a different request."
                    )
                return self._replay(identity, existing_id)

        task_id = f"task_{uuid.uuid4().hex[:16]}"
        trace_id = f"trc_{uuid.uuid4().hex[:16]}"
        with self.session_factory() as session:
            services = self._services(session)
            try:
                task = services.tasks.create(
                    task_id=task_id,
                    tenant_id=identity.tenant_id,
                    user_id=identity.user_id,
                    request=request,
                    context=context or {},
                    trace_id=trace_id,
                    idempotency_key=key,
                    request_digest=digest,
                )
                services.tasks.append_event(
                    task.id, "task.created", {"status": task.status.value}
                )
                AuditRepository(session).record(
                    actor_id=identity.user_id,
                    tenant_id=identity.tenant_id,
                    action="task.create",
                    resource_type="task",
                    resource_id=task.id,
                    outcome="SUCCESS",
                )
                session.commit()
            except IntegrityError:
                # The repository flushes, so the constraint can fire before
                # commit. Another worker won the same key; the constraint,
                # not application timing, decides which task survives.
                session.rollback()
                existing = TaskRepository(session).find_by_idempotency(
                    identity.tenant_id, identity.user_id, key or ""
                )
                if existing is None:
                    raise
                if existing.request_digest != digest:
                    raise ConflictError(
                        "Idempotency-Key was reused with a different request."
                    ) from None
                return self._replay(identity, existing.id)
        self._run(task_id)
        return self.get_task(identity, task_id)

    def _replay(self, identity: Identity, task_id: str) -> dict[str, Any]:
        """Return the task a retry already created; resume it if it never ran."""
        self._run(task_id)
        snapshot = self.get_task(identity, task_id)
        snapshot["idempotent_replay"] = True
        return snapshot

    def _run(self, task_id: str) -> None:
        with self.session_factory() as session:
            services = self._services(session)
            task = services.tasks.require(task_id)
            if task.status is not TaskStatus.CREATED:
                return
            services.tasks.transition(
                task, TaskStatus.RUNNING, allowed_from={TaskStatus.CREATED}
            )
            services.tasks.append_event(task.id, "task.running", {})
            session.commit()

            identity = Identity(task.tenant_id, task.user_id, frozenset())
            plan, planning_source, model_trace = self.brain.plan(
                task.request, task.context
            )
            trace = TraceRecorder(tenant_id=task.tenant_id, trace_id=task.trace_id)
            route_attributes: dict[str, Any] = {
                "intent": plan.intent.value,
                "order_id": plan.order_id,
                "amount_cents": plan.amount_cents,
                "planning_source": planning_source,
            }
            routed_payload: dict[str, Any] = {
                "intent": plan.intent.value,
                "order_id": plan.order_id,
                "planning_source": planning_source,
            }
            if model_trace is not None:
                route_attributes.update(model_trace.as_dict())
                routed_payload["model"] = model_trace.as_dict()
                # Same transaction as task.routed: a task that was planned by
                # the model and a usage row either both exist or neither does.
                LlmUsageService(session).record(
                    tenant_id=task.tenant_id,
                    user_id=task.user_id,
                    task_id=task.id,
                    source="task",
                    planning_source=planning_source,
                    model_name=getattr(self.brain, "model_name", None),
                    requests=model_trace.requests,
                    retries=model_trace.retries,
                    input_tokens=model_trace.input_tokens,
                    output_tokens=model_trace.output_tokens,
                    latency_ms=model_trace.latency_ms,
                    status=model_trace.status,
                )
            trace.add("understand", "task.route", attributes=route_attributes)
            services.tasks.append_event(task.id, "task.routed", routed_payload)
            session.commit()

            try:
                self._dispatch(session, services, task, identity, plan, trace)
            except ClarificationRequired as exc:
                session.rollback()
                task = services.tasks.require(task_id)
                self._clarify(services, task, exc.message)
                session.commit()
            except AppError as exc:
                session.rollback()
                task = services.tasks.require(task_id)
                self._mark_failed(
                    services,
                    task,
                    summary=exc.message,
                    error_code=exc.code,
                    allowed_from={TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL},
                )
                session.commit()
            except Exception:
                logger.exception("task dispatch failed task_id=%s", task_id)
                session.rollback()
                task = services.tasks.require(task_id)
                self._mark_failed(
                    services,
                    task,
                    summary="任务执行失败，已安全终止；请稍后重试或转人工。",
                    error_code="INTERNAL_ERROR",
                    allowed_from={TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL},
                )
                session.commit()
            TraceRepository(session).flush(trace)
            session.commit()

    def _dispatch(
        self,
        session: Session,
        services: RuntimeServices,
        task: Task,
        identity: Identity,
        plan: RoutePlan,
        trace: TraceRecorder,
    ) -> None:
        if plan.intent is Intent.ORDER_QUERY:
            if not plan.order_id:
                self._clarify(
                    services, task, "请提供订单号（例如 ORD-DEMO-1001），我再为你查询订单。"
                )
                session.commit()
                return
            result = self._call_tool(
                services, task, identity, "get_order", {"order_id": plan.order_id}, trace
            )
            self._finish(
                services,
                task,
                TaskStatus.SUCCEEDED,
                {
                    "outcome": VerificationStatus.VERIFIED_SUCCESS.value,
                    "summary": result.message,
                    "evidence": [{"type": "order", "reference": plan.order_id}],
                    "checks": [{"name": "order_authorized_and_found", "passed": True}],
                    "data": result.data,
                },
            )
            session.commit()
            return

        if plan.intent is Intent.LOGISTICS_QUERY:
            if not plan.order_id:
                self._clarify(
                    services, task, "请提供订单号（例如 ORD-DEMO-1001），我再为你查询物流。"
                )
                session.commit()
                return
            result = self._call_tool(
                services,
                task,
                identity,
                "get_logistics",
                {"order_id": plan.order_id},
                trace,
            )
            outcome = (
                VerificationStatus.VERIFIED_SUCCESS
                if result.data.get("available")
                else VerificationStatus.NEEDS_HUMAN_REVIEW
            )
            self._finish(
                services,
                task,
                TaskStatus.SUCCEEDED,
                {
                    "outcome": outcome.value,
                    "summary": result.message,
                    "evidence": [{"type": "order", "reference": plan.order_id}],
                    "checks": [
                        {
                            "name": "logistics_present",
                            "passed": bool(result.data.get("available")),
                        }
                    ],
                    "data": result.data,
                },
            )
            session.commit()
            return

        if plan.intent is Intent.POLICY_QA:
            result = self._call_tool(
                services,
                task,
                identity,
                "search_policy",
                {"query": task.request[:1000]},
                trace,
            )
            items = result.data.get("results", [])
            if not items:
                self._finish(
                    services,
                    task,
                    TaskStatus.SUCCEEDED,
                    {
                        "outcome": VerificationStatus.NEEDS_HUMAN_REVIEW.value,
                        "summary": "未检索到适用的有效政策，无法给出确定结论；建议转人工核实。",
                        "evidence": [],
                        "checks": [{"name": "active_policy_found", "passed": False}],
                    },
                )
                session.commit()
                return
            top = items[0]
            snippet = top["content"][:280].replace("\n", " ")
            section_label = f"「{top['section']}」" if top["section"] else ""
            self._finish(
                services,
                task,
                TaskStatus.SUCCEEDED,
                {
                    "outcome": VerificationStatus.VERIFIED_SUCCESS.value,
                    "summary": (
                        f"根据《{top['title']}》{top['version']} {section_label}：{snippet}"
                    ),
                    "evidence": [
                        {k: v for k, v in item.items() if k != "content"} for item in items
                    ],
                    "checks": [
                        {"name": "citations_present", "passed": True},
                        {"name": "documents_effective", "passed": True},
                    ],
                    "data": {"answer_mode": "extractive"},
                },
            )
            session.commit()
            return

        if plan.intent is Intent.TICKET_CREATE:
            category = "REFUND" if "退款" in task.request else "OTHER"
            result = self._call_tool(
                services,
                task,
                identity,
                "create_ticket",
                {
                    "category": category,
                    "priority": "HIGH" if "投诉" in task.request else "NORMAL",
                    "subject": task.request[:80],
                    "description": task.request[:2000],
                    "idempotency_key": f"task:{task.id}",
                },
                idempotency_key=f"task:{task.id}",
                trace=trace,
            )
            self._finish(
                services,
                task,
                TaskStatus.SUCCEEDED,
                {
                    "outcome": VerificationStatus.VERIFIED_SUCCESS.value,
                    "summary": result.message,
                    "evidence": [
                        {
                            "type": "ticket",
                            "reference": result.data.get("ticket_no"),
                            "ticket_id": result.data.get("ticket_id"),
                        }
                    ],
                    "checks": [
                        {"name": "ticket_created", "passed": bool(result.data.get("ticket_id"))}
                    ],
                    "data": result.data,
                },
            )
            session.commit()
            return

        if plan.intent is Intent.REFUND_REQUEST:
            self._handle_refund(session, services, task, identity, plan, trace)
            return

        self._clarify(
            services,
            task,
            "我暂时无法判断这个请求属于售后范围。请补充订单号、具体问题，或选择转人工。",
        )
        session.commit()

    def _handle_refund(
        self,
        session: Session,
        services: RuntimeServices,
        task: Task,
        identity: Identity,
        plan: RoutePlan,
        trace: TraceRecorder,
    ) -> None:
        if not plan.order_id:
            self._clarify(services, task, "申请退款需要订单号，请提供订单号后重试。")
            session.commit()
            return
        if plan.amount_invalid:
            self._clarify(services, task, "退款金额格式不正确，请提供大于 0 且最多两位小数的金额。")
            session.commit()
            return
        if plan.amount_cents is None:
            self._clarify(services, task, "申请退款需要退款金额，请提供具体金额（单位：元）。")
            session.commit()
            return

        eligibility = self._call_tool(
            services,
            task,
            identity,
            "check_refund_eligibility",
            {"order_id": plan.order_id, "amount_cents": plan.amount_cents},
            trace,
        ).data
        if not eligibility["eligible"]:
            reasons = "、".join(eligibility["reasons"])
            self._finish(
                services,
                task,
                TaskStatus.SUCCEEDED,
                {
                    "outcome": VerificationStatus.FAILED.value,
                    "summary": f"该订单当前不符合退款条件（{reasons}），未创建退款申请。",
                    "evidence": [{"type": "order", "reference": plan.order_id}],
                    "checks": [
                        {
                            "name": "refund_eligible",
                            "passed": False,
                            "reasons": eligibility["reasons"],
                        }
                    ],
                    "data": {"eligibility": eligibility},
                },
            )
            session.commit()
            return

        order = services.orders.require_order(identity, plan.order_id)
        amount = int(eligibility["amount_cents"])
        payload = refund_payload(order, amount, eligibility["policy_version"])
        digest = canonical_digest(payload)
        services.executions.create_pending(
            task_id=task.id,
            tool_name="request_refund",
            idempotency_key=f"refund:{task.id}",
            request_digest=digest,
            request_payload=payload,
        )
        approval = services.approval_service.create_refund_approval(
            task_id=task.id,
            order=order,
            amount_cents=amount,
            requested_by=identity.user_id,
            policy_version=eligibility["policy_version"],
        )
        services.tasks.transition(
            task,
            TaskStatus.WAITING_APPROVAL,
            allowed_from={TaskStatus.RUNNING},
            result={
                "outcome": "WAITING_APPROVAL",
                "summary": (
                    f"已查询订单并完成资格校验。退款 {format_cents(amount)} {order.currency} "
                    "需要人工审批，审批通过后才会执行。"
                ),
                "evidence": [
                    {"type": "order", "reference": order.id},
                    {
                        "type": "policy",
                        "document_id": "POLICY-REFUND",
                        "version": eligibility["policy_version"],
                    },
                ],
                "pending_action": {
                    "type": "REFUND",
                    "approval_id": approval.id,
                    "amount_cents": amount,
                    "currency": order.currency,
                    "requires_approval": True,
                },
            },
        )
        services.tasks.append_event(
            task.id,
            "approval.requested",
            {"approval_id": approval.id, "amount_cents": amount, "order_id": order.id},
        )
        trace.add(
            "approval",
            "refund.approval_requested",
            attributes={
                "approval_id": approval.id,
                "amount_cents": amount,
                "order_id": order.id,
                "policy_version": eligibility["policy_version"],
            },
        )
        session.commit()

    def approve(
        self,
        identity: Identity,
        task_id: str,
        *,
        approval_id: str | None,
        decision: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        if not identity.has_role("approver"):
            raise ForbiddenError("Approver role required.")
        with self.session_factory() as session:
            services = self._services(session)
            task = services.tasks.require(task_id)
            self._require_visible(task, identity)
            if task.status is not TaskStatus.WAITING_APPROVAL:
                raise ConflictError(f"Task is {task.status.value}, not waiting for approval.")
            trace = TraceRecorder(tenant_id=task.tenant_id, trace_id=task.trace_id)
            pending = services.approvals.pending_for_task(task.id)
            if pending is None:
                raise ConflictError("No pending approval for this task.")
            chosen_id = approval_id or pending.id
            if chosen_id != pending.id:
                raise ConflictError("Approval is not the pending action for this task.")

            try:
                approval = services.approval_service.decide(
                    task_id=task.id,
                    approval_id=chosen_id,
                    decision=decision,
                    reviewer=identity,
                    reason=reason,
                )
            except AppError as exc:
                if exc.code == "APPROVAL_EXPIRED":
                    services.executions.cancel_pending(task.id, reason="APPROVAL_EXPIRED")
                    self._finish(
                        services,
                        task,
                        TaskStatus.EXPIRED,
                        {
                            "outcome": VerificationStatus.NEEDS_HUMAN_REVIEW.value,
                            "summary": "审批已过期，未执行退款；如需继续请重新发起申请。",
                            "evidence": [],
                            "checks": [{"name": "approval_valid", "passed": False}],
                        },
                        allowed_from={TaskStatus.WAITING_APPROVAL},
                    )
                    trace.add(
                        "approval",
                        "refund.approval_expired",
                        attributes={"approval_id": chosen_id, "error_code": exc.code},
                        status="ERROR",
                    )
                    TraceRepository(session).flush(trace)
                    session.commit()
                raise

            if approval.status is ApprovalStatus.REJECTED:
                services.executions.cancel_pending(task.id, reason="APPROVAL_REJECTED")
                self._finish(
                    services,
                    task,
                    TaskStatus.REJECTED,
                    {
                        "outcome": VerificationStatus.FAILED.value,
                        "summary": "审批未通过，未执行退款。",
                        "evidence": [],
                        "checks": [{"name": "approval_approved", "passed": False}],
                    },
                    allowed_from={TaskStatus.WAITING_APPROVAL},
                )
                services.tasks.append_event(
                    task.id, "approval.rejected", {"approval_id": approval.id}
                )
                trace.add(
                    "approval",
                    "refund.approval_rejected",
                    attributes={
                        "approval_id": approval.id,
                        "reviewer_id": identity.user_id,
                        "reason": reason,
                    },
                )
                TraceRepository(session).flush(trace)
                session.commit()
                return self._snapshot(services, task)

            services.tasks.transition(
                task, TaskStatus.RUNNING, allowed_from={TaskStatus.WAITING_APPROVAL}
            )
            services.tasks.append_event(task.id, "approval.approved", {"approval_id": approval.id})
            trace.add(
                "approval",
                "refund.approval_approved",
                attributes={
                    "approval_id": approval.id,
                    "reviewer_id": identity.user_id,
                    "reason": reason,
                },
            )
            session.commit()

            outcome = services.refunds.execute(task.id, approval.id)
            self._apply_refund_outcome(services, task, outcome)
            trace.add(
                "reconcile",
                "refund.execution",
                attributes={
                    "verification_status": outcome.status.value,
                    "reason_code": outcome.reason_code,
                    "refund_id": outcome.refund_id,
                },
                status="OK" if outcome.status is VerificationStatus.VERIFIED_SUCCESS else "UNKNOWN",
            )
            TraceRepository(session).flush(trace)
            session.commit()
            return self._snapshot(services, task)

    def cancel(self, identity: Identity, task_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            services = self._services(session)
            task = services.tasks.require(task_id)
            self._require_visible(task, identity)
            if task.status is TaskStatus.CANCELLED:
                return self._snapshot(services, task)
            if task.status in TERMINAL:
                raise ConflictError(f"Task already {task.status.value}; cannot cancel.")
            pending = services.approvals.pending_for_task(task.id)
            if pending is not None:
                services.approvals.resolve(
                    pending,
                    ApprovalStatus.CANCELLED,
                    reviewer_id=identity.user_id,
                    reason="task cancelled",
                )
            services.executions.cancel_pending(task.id, reason="TASK_CANCELLED")
            self._finish(
                services,
                task,
                TaskStatus.CANCELLED,
                {
                    "outcome": VerificationStatus.FAILED.value,
                    "summary": "任务已取消。",
                    "evidence": [],
                    "checks": [],
                },
                allowed_from={TaskStatus.CREATED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL},
            )
            session.commit()
            return self._snapshot(services, task)

    def reconcile(self, task_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            services = self._services(session)
            self._reconcile_with(services, task_id)
            session.commit()
            task = services.tasks.require(task_id)
            return self._snapshot(services, task)

    def deliver_outbox(self, *, limit: int = 50) -> DeliveryReport:
        """Work the outbox queue once.

        The queue holds the debts the request path could not settle: a refund
        whose result nobody knows, and a proactive notice the customer already
        confirmed. Each refund is reconciled against the authoritative record;
        a refund that is still unconfirmed is not a delivered message, so the
        row waits for the next pass and one that never confirms becomes the
        dead letter an operator can see. Each notice is handed to the outbound
        transport, and only a transport that accepts it lets the row reach
        ``SENT``.
        """
        with self.session_factory() as session:
            services = self._services(session)
            return services.outbox.deliver(self._outbox_sink(session), limit=limit)

    def _outbox_sink(self, session: Session) -> OutboxSink:
        """What the queue knows how to deliver: a refund to settle, a notice to send."""

        def sink(row: OutboxMessage) -> None:
            if row.event_type == PROACTIVE_EVENT_TYPE:
                ProactiveSender(session, self.settings).deliver(row)
                return
            if row.event_type != REFUND_PENDING_CONFIRMATION:
                raise MessageNotDelivered(f"No handler for {row.event_type}.")
            task_id = str(row.payload.get("task_id", ""))
            if not task_id:
                raise MessageNotDelivered("Message carries no task id.")
            outcome = self._reconcile_refund(task_id)
            if outcome.status is VerificationStatus.PENDING_CONFIRMATION:
                raise MessageNotDelivered(
                    f"Refund {task_id} is still unconfirmed "
                    f"({outcome.reason_code or 'NOT_FOUND'})."
                )

        return sink

    def _reconcile_refund(self, task_id: str) -> RefundOutcome:
        """Reconcile one task in a session of its own.

        The worker holds the outbox claim in the session it started
        with. A second session keeps the reconciliation and the claim
        from fighting over one transaction.
        """
        with self.session_factory() as session:
            services = self._services(session)
            outcome = self._reconcile_with(services, task_id)
            session.commit()
            return outcome

    def _reconcile_with(
        self, services: RuntimeServices, task_id: str
    ) -> RefundOutcome:
        task = services.tasks.require(task_id)
        outcome = services.refunds.reconcile_task(task.id)
        self._apply_refund_outcome(services, task, outcome)
        services.tasks.append_event(
            task.id,
            "refund.reconciled",
            {"status": outcome.status.value, "reason_code": outcome.reason_code},
        )
        return outcome

    def get_task(self, identity: Identity, task_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            services = self._services(session)
            task = services.tasks.require(task_id)
            self._require_visible(task, identity)
            return self._snapshot(services, task)

    def list_tasks(
        self, identity: Identity, *, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            services = self._services(session)
            rows = services.tasks.list_for_user(
                identity.tenant_id, identity.user_id, limit=limit, offset=offset
            )
            total = services.tasks.count_for_user(identity.tenant_id, identity.user_id)
            items = []
            for task in rows:
                result = task.result or {}
                items.append(
                    {
                        "task_id": task.id,
                        "status": task.status.value,
                        "request": task.request,
                        "outcome": result.get("outcome"),
                        "summary": result.get("summary"),
                        "created_at": task.created_at.isoformat(),
                        "updated_at": task.updated_at.isoformat(),
                    }
                )
            return {"items": items, "total": total, "limit": limit, "offset": offset}

    def list_approvals(
        self,
        identity: Identity,
        *,
        statuses: list[ApprovalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        require_approver(identity)
        with self.session_factory() as session:
            services = self._services(session)
            rows = services.approvals.list_for_tenant(
                identity.tenant_id, statuses=statuses, limit=limit, offset=offset
            )
            total = services.approvals.count_for_tenant(
                identity.tenant_id, statuses=statuses
            )
            counts = services.approvals.counts_for_tenant(identity.tenant_id)
            return {
                "items": [
                    self._approval_snapshot(approval, task, order)
                    for approval, task, order in rows
                ],
                "total": total,
                "limit": limit,
                "offset": offset,
                "counts": counts,
            }

    def get_approval(self, identity: Identity, approval_id: str) -> dict[str, Any]:
        require_approver(identity)
        with self.session_factory() as session:
            services = self._services(session)
            row = services.approvals.get_for_tenant(approval_id, identity.tenant_id)
            if row is None:
                raise NotFoundError("Approval not found.")
            approval, task, order = row
            return self._approval_snapshot(approval, task, order, detail=True)

    def events(
        self, identity: Identity, task_id: str, after_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            services = self._services(session)
            task = services.tasks.require(task_id)
            self._require_visible(task, identity)
            rows = services.tasks.events(task.id, after_id=after_id)
            return [
                {
                    "id": row.id,
                    "event": row.event_type,
                    "data": row.payload,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    def _apply_refund_outcome(
        self, services: RuntimeServices, task: Task, outcome: RefundOutcome
    ) -> None:
        status = outcome.status
        if status is VerificationStatus.VERIFIED_SUCCESS:
            task_status = TaskStatus.SUCCEEDED
        elif status is VerificationStatus.PENDING_CONFIRMATION:
            task_status = TaskStatus.SUCCEEDED
        else:
            task_status = TaskStatus.FAILED
        current = task.status
        allowed = {TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL, TaskStatus.SUCCEEDED}
        if current not in allowed:
            allowed = {current}
        self._finish(
            services,
            task,
            task_status,
            {
                "outcome": status.value,
                "summary": outcome.summary,
                "evidence": (
                    [{"type": "refund", "reference": outcome.refund_id}]
                    if outcome.refund_id
                    else []
                ),
                "checks": list(outcome.checks),
                "reason_code": outcome.reason_code,
            },
            allowed_from=allowed,
        )

    def _call_tool(
        self,
        services: RuntimeServices,
        task: Task,
        identity: Identity,
        name: str,
        args: dict[str, Any],
        trace: TraceRecorder | None = None,
        *,
        idempotency_key: str | None = None,
        approval_id: str | None = None,
    ) -> ToolResult:
        services.tasks.append_event(task.id, "tool.call_started", {"tool": name})
        started = time.perf_counter()
        manifest = services.gateway.registry.require(name).manifest
        try:
            outcome = services.gateway.call_outcome(
                name,
                ToolContext(
                    identity=identity,
                    task_id=task.id,
                    approval_id=approval_id,
                    idempotency_key=idempotency_key,
                ),
                args,
            )
        except AppError as exc:
            # A rejected argument never reaches the executor, so this failure
            # has no ExecutionOutcome to report; record it here so the trace
            # still shows which tool was attempted with which parameters.
            if trace is not None:
                step = _degradation_step(manifest.degradation, exc)
                trace.add(
                    "tool",
                    name,
                    attributes={
                        "tool": name,
                        "params": args,
                        "params_digest": canonical_digest(args),
                        "ok": False,
                        "error_code": exc.code,
                        "failure_kind": (
                            exc.kind.value
                            if isinstance(exc, ToolExecutionError)
                            else None
                        ),
                        "attempts": getattr(exc, "attempts", 0),
                        "degradation": step.kind if step is not None else None,
                        "timeout_ms": manifest.timeout_ms,
                    },
                    status="ERROR",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            self._degrade(services, task, name, exc, trace, started)
            raise
        except Exception as exc:
            if trace is not None:
                trace.add(
                    "tool",
                    name,
                    attributes={
                        "tool": name,
                        "params": args,
                        "error": type(exc).__name__,
                    },
                    status="ERROR",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            raise
        result = outcome.result
        if trace is not None:
            trace.add(
                "tool",
                name,
                attributes={
                    "tool": name,
                    "params": args,
                    "params_digest": canonical_digest(args),
                    "ok": result.ok,
                    "error_code": result.error_code,
                    "timeout_ms": manifest.timeout_ms,
                    "attempts": outcome.attempts,
                    "over_budget": outcome.over_budget,
                    "circuit_state": outcome.circuit_state,
                },
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        services.tasks.append_event(
            task.id,
            "tool.call_completed",
            {"tool": name, "ok": result.ok, "message": result.message[:200]},
        )
        return result

    def _degrade(
        self,
        services: RuntimeServices,
        task: Task,
        name: str,
        exc: AppError,
        trace: TraceRecorder | None,
        started: float,
    ) -> None:
        """Turn a tool failure into the explicit degradation the manifest declares.

        A malformed argument is a question, not a failed task: the user is
        asked for the missing slot instead of being shown an error.
        """
        manifest = services.gateway.registry.require(name).manifest
        step = _degradation_step(manifest.degradation, exc)
        if step is None:
            return
        if trace is not None:
            trace.add(
                "guard",
                f"tool.degraded.{name}",
                attributes={
                    "tool": name,
                    "degradation": step.kind,
                    "message": step.message,
                    "error_code": exc.code,
                },
                status="ERROR",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        services.tasks.append_event(
            task.id,
            "tool.degraded",
            {"tool": name, "degradation": step.kind, "message": step.message},
        )
        if step.kind == "ask_user":
            raise ClarificationRequired(step.message, details={"tool": name}) from exc

    def _clarify(self, services: RuntimeServices, task: Task, summary: str) -> None:
        self._finish(
            services,
            task,
            TaskStatus.SUCCEEDED,
            {
                "outcome": VerificationStatus.NEEDS_HUMAN_REVIEW.value,
                "summary": summary,
                "evidence": [],
                "checks": [{"name": "required_slots_present", "passed": False}],
            },
            allowed_from={TaskStatus.RUNNING},
        )

    def _mark_failed(
        self,
        services: RuntimeServices,
        task: Task,
        *,
        summary: str,
        error_code: str,
        allowed_from: set[TaskStatus],
    ) -> None:
        if task.status in TERMINAL:
            return
        pending = services.approvals.pending_for_task(task.id)
        if pending is not None:
            services.approvals.resolve(
                pending,
                ApprovalStatus.CANCELLED,
                reviewer_id="system",
                reason=f"task failed: {error_code}",
            )
        services.executions.cancel_pending(task.id, reason="TASK_FAILED")
        self._finish(
            services,
            task,
            TaskStatus.FAILED,
            {
                "outcome": VerificationStatus.FAILED.value,
                "summary": summary,
                "evidence": [],
                "checks": [],
                "error_code": error_code,
            },
            allowed_from=allowed_from,
        )

    def _finish(
        self,
        services: RuntimeServices,
        task: Task,
        status: TaskStatus,
        result: dict[str, Any],
        *,
        allowed_from: set[TaskStatus] | None = None,
    ) -> None:
        services.tasks.transition(
            task,
            status,
            allowed_from=allowed_from or {TaskStatus.RUNNING},
            result=result,
        )
        services.tasks.append_event(
            task.id,
            f"task.{status.value.lower()}",
            {"outcome": result.get("outcome")},
        )

    def _require_visible(self, task: Task, identity: Identity) -> None:
        if task.tenant_id != identity.tenant_id:
            raise NotFoundError("Task not found.")
        if task.user_id != identity.user_id and not (
            identity.has_role("approver") or identity.has_role("support")
        ):
            raise NotFoundError("Task not found.")

    def _approval_snapshot(
        self,
        approval: Approval,
        task: Task,
        order: Order | None,
        *,
        detail: bool = False,
    ) -> dict[str, Any]:
        payload = dict(approval.action_payload or {})
        result = task.result or {}
        snapshot: dict[str, Any] = {
            "approval_id": approval.id,
            "task_id": approval.task_id,
            "status": approval.status.value,
            "action_type": approval.action_type,
            "amount_cents": int(payload.get("amount_cents", 0)),
            "currency": str(payload.get("currency", "CNY")),
            "order_id": approval.resource_id,
            "requested_by": approval.requested_by,
            "reviewer_id": approval.reviewer_id,
            "reason": approval.reason,
            "resource_version": approval.resource_version,
            "created_at": approval.created_at.isoformat(),
            "expires_at": approval.expires_at.isoformat(),
            "resolved_at": (
                approval.resolved_at.isoformat() if approval.resolved_at else None
            ),
            "request": task.request,
            "task_status": task.status.value,
            "task_outcome": result.get("outcome"),
            "order": (
                {
                    "order_id": order.id,
                    "user_id": order.user_id,
                    "status": order.status.value,
                    "total_cents": order.total_cents,
                    "refundable_cents": order.refundable_cents,
                    "currency": order.currency,
                    "version": order.version,
                    "paid_at": order.paid_at.isoformat(),
                    "delivered_at": (
                        order.delivered_at.isoformat() if order.delivered_at else None
                    ),
                }
                if order is not None
                else None
            ),
        }
        if detail:
            snapshot["action_digest"] = approval.action_digest
            snapshot["action_payload"] = payload
        return snapshot

    def _snapshot(self, services: RuntimeServices, task: Task) -> dict[str, Any]:
        pending = services.approvals.pending_for_task(task.id)
        latest = services.approvals.latest_for_task(task.id)
        execution = services.executions.latest(task.id)
        return {
            "task_id": task.id,
            "status": task.status.value,
            "request": task.request,
            "context": task.context,
            "result": task.result,
            "version": task.version,
            "trace_id": task.trace_id,
            "idempotent_replay": False,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
            "pending_approval": (
                {
                    "approval_id": pending.id,
                    "action_type": pending.action_type,
                    "status": pending.status.value,
                    "expires_at": pending.expires_at.isoformat(),
                    "amount_cents": (pending.action_payload or {}).get("amount_cents"),
                }
                if pending
                else None
            ),
            "latest_approval": (
                {
                    "approval_id": latest.id,
                    "status": latest.status.value,
                    "reviewer_id": latest.reviewer_id,
                }
                if latest
                else None
            ),
            "tool_execution": (
                {
                    "id": execution.id,
                    "tool_name": execution.tool_name,
                    "status": execution.status.value,
                    "attempt": execution.attempt,
                }
                if execution
                else None
            ),
        }
