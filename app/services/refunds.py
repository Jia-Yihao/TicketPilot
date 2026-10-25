from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.policy import PolicyEngine
from app.core.security import Identity
from app.models.approval import Approval
from app.models.commerce import Order, Refund
from app.models.enums import (
    ApprovalStatus,
    RefundStatus,
    ToolExecutionStatus,
    VerificationStatus,
)
from app.models.tool_execution import ToolExecution
from app.repositories.approvals import ApprovalRepository
from app.repositories.audit import AuditRepository
from app.repositories.commerce import CommerceRepository
from app.repositories.tasks import TaskRepository
from app.repositories.tool_executions import ToolExecutionRepository
from app.services.approvals import ApprovalService
from app.services.outbox import OutboxService
from app.services.refund_policy import RefundPolicy
from app.services.verification import VerificationService


class ProviderTimeout(Exception):
    pass


class ProviderUnknown(Exception):
    pass


class ProviderFailure(Exception):
    pass


@dataclass(frozen=True, slots=True)
class RefundOutcome:
    status: VerificationStatus
    summary: str
    refund_id: str | None = None
    ledger_id: str | None = None
    checks: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    reason_code: str | None = None
    amount_cents: int | None = None
    currency: str | None = None


class MockRefundProvider:
    """Idempotent mock provider with injectable failure modes."""

    def __init__(self, session: Session, *, mode: str, delay_seconds: float = 0.0) -> None:
        self.repo = CommerceRepository(session)
        self.mode = mode
        self.delay_seconds = delay_seconds

    def query(self, idempotency_key: str) -> Refund | None:
        return self.repo.get_refund_by_idempotency(idempotency_key)

    def request(self, order: Order, amount_cents: int, idempotency_key: str) -> Refund:
        existing = self.query(idempotency_key)
        if existing is not None:
            return existing
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)

        if self.mode == "fail":
            self._create(order, amount_cents, idempotency_key, RefundStatus.FAILED)
            raise ProviderFailure("Mock provider rejected the refund.")
        if self.mode == "timeout_before":
            raise ProviderTimeout("Mock provider timed out before committing.")
        if self.mode == "unknown":
            raise ProviderUnknown("Mock provider returned an unknown status.")

        refund = self._create(order, amount_cents, idempotency_key, RefundStatus.SUCCEEDED)
        if self.mode == "timeout_after":
            raise ProviderTimeout("Mock provider committed but the response timed out.")
        return refund

    def _create(
        self, order: Order, amount_cents: int, idempotency_key: str, status: RefundStatus
    ) -> Refund:
        token = uuid.uuid4().hex[:10]
        refund = Refund(
            id=f"rfd_{token}",
            order_id=order.id,
            tenant_id=order.tenant_id,
            user_id=order.user_id,
            amount_cents=amount_cents,
            status=status,
            external_refund_id=f"ext_{token}",
            idempotency_key=idempotency_key,
        )
        return self.repo.create_refund(refund)


class RefundExecutionService:
    def __init__(
        self,
        session: Session,
        *,
        mode: str = "success",
        delay_seconds: float = 0.0,
        approval_ttl_minutes: int = 30,
        engine: PolicyEngine,
    ) -> None:
        self.session = session
        self.tasks = TaskRepository(session)
        self.approvals = ApprovalRepository(session)
        self.approval_service = ApprovalService(session, ttl_minutes=approval_ttl_minutes)
        self.executions = ToolExecutionRepository(session)
        self.commerce = CommerceRepository(session)
        self.audit = AuditRepository(session)
        self.provider = MockRefundProvider(session, mode=mode, delay_seconds=delay_seconds)
        self.verifier = VerificationService()
        self.policy = RefundPolicy(session, engine)
        self.outbox = OutboxService(session)

    def execute(self, task_id: str, approval_id: str) -> RefundOutcome:
        task = self.tasks.require(task_id)
        approval = self.approvals.require(approval_id)
        payload = dict(approval.action_payload or {})
        amount = int(payload.get("amount_cents", 0))
        order_id = str(payload.get("order_id", ""))
        ledger = self.executions.get_by_idempotency(task_id, f"refund:{task_id}")

        if approval.task_id != task_id:
            raise NotFoundError("Approval not found for this task.")
        if (
            ledger is not None
            and ledger.status is ToolExecutionStatus.PENDING
            and approval.status is not ApprovalStatus.APPROVED
        ):
            raise ConflictError(f"Approval is {approval.status.value}, not APPROVED.")

        if not order_id or ledger is None:
            return RefundOutcome(
                VerificationStatus.NEEDS_HUMAN_REVIEW,
                "退款申请数据不完整，已转人工复核，未执行退款。",
                reason_code="MISSING_EXECUTION_CONTEXT",
            )

        if ledger.status is ToolExecutionStatus.RECONCILED:
            order = self.commerce.get_order_for_update(order_id)
            if order is None:
                return RefundOutcome(
                    VerificationStatus.NEEDS_HUMAN_REVIEW,
                    "退款申请数据不完整，已转人工复核，未执行退款。",
                    reason_code="MISSING_EXECUTION_CONTEXT",
                )
            refund = self.provider.query(ledger.idempotency_key)
            return self._finalize(
                ledger,
                refund,
                order,
                amount,
                already_reconciled=True,
                approval=approval,
            )

        if ledger.status in {ToolExecutionStatus.SENT, ToolExecutionStatus.UNKNOWN}:
            return self.reconcile_task(task_id)

        order = self.commerce.get_order_for_update(order_id)
        if order is None:
            return RefundOutcome(
                VerificationStatus.NEEDS_HUMAN_REVIEW,
                "退款申请数据不完整，已转人工复核，未执行退款。",
                reason_code="MISSING_EXECUTION_CONTEXT",
            )

        foreign = self.commerce.blocking_refund_except(order.id, ledger.idempotency_key)
        if foreign is not None:
            self.approval_service.invalidate(approval, reason="ORDER_REFUND_IN_PROGRESS")
            ledger.status = ToolExecutionStatus.CANCELLED
            ledger.error_code = "ORDER_REFUND_IN_PROGRESS"
            ledger.error_message = f"another refund exists for this order: {foreign.id}"
            self.session.commit()
            return RefundOutcome(
                VerificationStatus.NEEDS_HUMAN_REVIEW,
                "该订单已有退款正在处理或已经成功，本次未重复执行；已转人工复核。",
                ledger_id=ledger.id,
                reason_code="ORDER_REFUND_IN_PROGRESS",
            )

        bound, reason = self.approval_service.verify_binding(approval, order)
        if not bound:
            self.approval_service.invalidate(approval, reason=reason)
            ledger.status = ToolExecutionStatus.CANCELLED
            ledger.error_code = reason
            self.session.commit()
            self.audit.record(
                actor_id=approval.reviewer_id or "system",
                tenant_id=order.tenant_id,
                action="refund.invalidated",
                resource_type="order",
                resource_id=order.id,
                outcome="REJECTED",
                detail={"reason": reason, "task_id": task_id},
            )
            return RefundOutcome(
                VerificationStatus.NEEDS_HUMAN_REVIEW,
                "审批绑定的订单版本已变化，旧审批失效，未执行退款；已转人工复核。",
                ledger_id=ledger.id,
                reason_code=reason,
            )

        identity = Identity(tenant_id=task.tenant_id, user_id=task.user_id, roles=frozenset())
        eligibility = self.policy.check(identity, order.id, amount)
        if not eligibility.eligible:
            self.approval_service.invalidate(approval, reason="ELIGIBILITY_CHANGED")
            ledger.status = ToolExecutionStatus.CANCELLED
            ledger.error_code = "ELIGIBILITY_CHANGED"
            self.session.commit()
            return RefundOutcome(
                VerificationStatus.NEEDS_HUMAN_REVIEW,
                "审批后复核发现退款资格已变化，未执行退款；已转人工复核。",
                ledger_id=ledger.id,
                reason_code="ELIGIBILITY_CHANGED",
            )

        if not self.executions.claim_for_send(ledger.id):
            self.session.rollback()
            return self.reconcile_task(task_id)
        self.session.commit()

        try:
            refund = self.provider.request(order, amount, ledger.idempotency_key)
        except ProviderFailure as exc:
            ledger = self.executions.get(ledger.id) or ledger
            ledger.status = ToolExecutionStatus.FAILED
            ledger.error_code = "PROVIDER_REJECTED"
            ledger.error_message = str(exc)
            self._consume_approval(
                approval, order=order, outcome=VerificationStatus.FAILED
            )
            self.session.commit()
            return RefundOutcome(
                VerificationStatus.FAILED,
                "退款服务明确拒绝本次申请，未产生退款。",
                ledger_id=ledger.id,
                reason_code="PROVIDER_REJECTED",
                currency=order.currency,
            )
        except (ProviderTimeout, ProviderUnknown) as exc:
            self.session.commit()
            ledger = self.executions.get(ledger.id) or ledger
            ledger.status = ToolExecutionStatus.UNKNOWN
            ledger.error_code = (
                "TOOL_TIMEOUT" if isinstance(exc, ProviderTimeout) else "TOOL_UNKNOWN"
            )
            ledger.error_message = str(exc)
            self._queue_pending_confirmation(
                task_id,
                identity,
                reason_code=ledger.error_code,
                ledger_id=ledger.id,
            )
            self.session.commit()
            return RefundOutcome(
                VerificationStatus.PENDING_CONFIRMATION,
                "退款请求已发出，但结果暂未确认，系统将通过对账核实；请勿重复提交。",
                ledger_id=ledger.id,
                reason_code=ledger.error_code,
                currency=order.currency,
            )

        ledger = self.executions.get(ledger.id) or ledger
        ledger.external_request_id = refund.external_refund_id
        return self._finalize(
            ledger,
            refund,
            order,
            amount,
            already_reconciled=False,
            approval=approval,
        )

    def reconcile_task(self, task_id: str) -> RefundOutcome:
        task = self.tasks.require(task_id)
        identity = Identity(tenant_id=task.tenant_id, user_id=task.user_id, roles=frozenset())
        ledger = self.executions.latest_for_task(task_id)
        if ledger is None:
            return RefundOutcome(
                VerificationStatus.NEEDS_HUMAN_REVIEW,
                "未找到对应的执行记录，已转人工复核。",
                reason_code="LEDGER_NOT_FOUND",
            )
        if ledger.status is ToolExecutionStatus.CANCELLED:
            return RefundOutcome(
                VerificationStatus.NEEDS_HUMAN_REVIEW,
                "执行记录已取消，未执行退款。",
                ledger_id=ledger.id,
                reason_code="LEDGER_CANCELLED",
            )
        if ledger.status is ToolExecutionStatus.FAILED:
            return RefundOutcome(
                VerificationStatus.FAILED,
                "退款服务已明确失败，未产生退款。",
                ledger_id=ledger.id,
                reason_code=ledger.error_code or "FAILED",
            )

        refund = self.provider.query(ledger.idempotency_key)
        order_id = str((ledger.request_payload or {}).get("order_id", ""))
        amount = int((ledger.request_payload or {}).get("amount_cents", 0))
        order = (
            self.commerce.get_order_for_update(order_id)
            if refund is not None
            else self.commerce.get_order(order_id)
        )
        if order is None:
            return RefundOutcome(
                VerificationStatus.NEEDS_HUMAN_REVIEW,
                "执行记录缺少订单信息，已转人工复核。",
                ledger_id=ledger.id,
                reason_code="ORDER_NOT_FOUND",
            )
        if refund is None:
            self._queue_pending_confirmation(
                task_id, identity, reason_code="NOT_FOUND_YET", ledger_id=ledger.id
            )
            return RefundOutcome(
                VerificationStatus.PENDING_CONFIRMATION,
                "对账暂未找到退款记录，结果仍未确认；系统会继续核实，请勿重复提交。",
                ledger_id=ledger.id,
                reason_code="NOT_FOUND_YET",
            )
        approval = self.approvals.latest_for_task(task_id)
        return self._finalize(
            ledger,
            refund,
            order,
            amount,
            already_reconciled=False,
            approval=approval,
        )

    def _finalize(
        self,
        ledger: ToolExecution,
        refund: Refund | None,
        order: Order,
        amount_cents: int,
        *,
        already_reconciled: bool,
        approval: Approval | None = None,
    ) -> RefundOutcome:
        if refund is None:
            return RefundOutcome(
                VerificationStatus.PENDING_CONFIRMATION,
                "对账暂未找到退款记录，结果仍未确认；请勿重复提交。",
                ledger_id=ledger.id,
                reason_code="NOT_FOUND_YET",
            )

        order = self.commerce.get_order_for_update(order.id) or order
        ledger = self.executions.get_for_update(ledger.id) or ledger
        payload = dict(ledger.response_payload or {})
        balance_applied = bool(payload.get("balance_applied"))
        balance_ok = True
        if refund.status is RefundStatus.SUCCEEDED and not balance_applied:
            if self.commerce.apply_refund_balance(order.id, amount_cents):
                self.session.refresh(order)
                balance_applied = True
            else:
                balance_ok = False

        payload.update(
            {
                "refund_id": refund.id,
                "external_refund_id": refund.external_refund_id,
                "amount_cents": amount_cents,
                "balance_applied": balance_applied,
                "order_version": order.version,
            }
        )
        ledger.response_payload = payload
        ledger.external_request_id = refund.external_refund_id
        ledger.status = (
            ToolExecutionStatus.RECONCILED
            if refund.status is RefundStatus.SUCCEEDED or already_reconciled
            else ToolExecutionStatus.FAILED
        )
        if refund.status is RefundStatus.FAILED:
            ledger.status = ToolExecutionStatus.FAILED
            ledger.error_code = "PROVIDER_REJECTED"

        verification = self.verifier.verify_refund(
            refund=refund, order=order, expected_amount_cents=amount_cents
        )
        checks = list(verification.checks)
        if not balance_ok:
            checks.append(
                {
                    "name": "refundable_balance_applied",
                    "passed": False,
                    "detail": "refundable balance insufficient after external success",
                }
            )
        status = verification.status
        if not balance_ok and status is VerificationStatus.VERIFIED_SUCCESS:
            status = VerificationStatus.NEEDS_HUMAN_REVIEW
        if status in {VerificationStatus.VERIFIED_SUCCESS, VerificationStatus.FAILED}:
            self._consume_approval(approval, order=order, outcome=status)
        self.session.commit()

        if status is VerificationStatus.VERIFIED_SUCCESS:
            summary = (
                f"退款 {amount_cents / 100:.2f} {order.currency} 已由权威退款记录确认，"
                f"退款单号 {refund.id}。"
            )
        elif status is VerificationStatus.PENDING_CONFIRMATION:
            summary = "退款请求已发出，但权威状态尚未确认；系统将通过对账收敛，请勿重复提交。"
        elif status is VerificationStatus.FAILED:
            summary = "退款未成功，权威退款记录显示失败。"
        else:
            summary = "退款记录存在异常，已转人工复核。"

        return RefundOutcome(
            status=status,
            summary=summary,
            refund_id=refund.id,
            ledger_id=ledger.id,
            checks=tuple(checks),
            reason_code=None if status is VerificationStatus.VERIFIED_SUCCESS else "VERIFY",
            amount_cents=(
                amount_cents if status is VerificationStatus.VERIFIED_SUCCESS else None
            ),
            currency=order.currency,
        )

    def _queue_pending_confirmation(
        self,
        task_id: str,
        identity: Identity,
        *,
        reason_code: str,
        ledger_id: str | None,
    ) -> None:
        """Write the pending-confirmation message with the fact, not after it.

        The row is flushed but not committed: the caller decides, so the
        message and the ledger state land together or not at all.
        """
        self.outbox.enqueue(
            identity=identity,
            aggregate_type="refund_task",
            aggregate_id=task_id,
            event_type="refund.pending_confirmation",
            dedupe_key=f"refund:{task_id}:pending_confirmation",
            payload={
                "task_id": task_id,
                "reason_code": reason_code,
                "ledger_id": ledger_id,
            },
        )

    def _consume_approval(
        self, approval: Approval | None, *, order: Order, outcome: VerificationStatus
    ) -> None:
        if approval is None or approval.status is not ApprovalStatus.APPROVED:
            return
        self.approvals.consume(
            approval, reason=f"consumed after execution: {outcome.value}"
        )
        self.audit.record(
            actor_id="system",
            tenant_id=order.tenant_id,
            action="approval.consume",
            resource_type="approval",
            resource_id=approval.id,
            outcome=ApprovalStatus.CONSUMED.value,
            detail={
                "task_id": approval.task_id,
                "order_id": order.id,
                "result": outcome.value,
            },
        )
