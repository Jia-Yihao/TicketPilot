"""退款的执行体：一笔退款怎么真的被退出去，以及退不成时怎么收场。

本模块只回答一个问题——**钱动了没有**。它不决定「要不要人批」（那是
``app/services/refund_policy.py`` 与 ``PolicyEngine`` 的判定），不建审批行
（那是 ``ApprovalService``），也不推进任务状态机（结果交回
``app/services/runtime.py`` 去落）。它管的是这条线的中段：校验审批行 / 台账
与订单版本的绑定，写执行台账，调退款提供方，再对着权威记录校验。

三种失败形态必须分开：**明确失败**（提供方说没退成）、**结果未知**（请求超时
或提供方返回不明）、**还没查到**。第一种可以判失败，后两种只能挂成
``PENDING_CONFIRMATION`` 等对账——把「未知」当「失败」处理，客户就会被重复
退款或重复扣款，那是这条路径上唯一不可挽回的错。

调用方是 ``runtime``（审批路径 ``execute``、自动路径 ``execute_auto``、
对账 ``reconcile_task``）和工具清单里的 ``request_refund``。上一层不该绕过
这里的绑定校验直接写台账：幂等与「这笔钱确实属于这份审批」都靠它成立。
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy.orm import Session

from app.core.approval_tiers import get_approval_tiers
from app.core.errors import ConflictError, NotFoundError
from app.core.policy import PolicyEngine
from app.core.security import Identity
from app.models.approval import Approval
from app.models.commerce import Order, Refund
from app.models.enums import (
    ApprovalStatus,
    RefundReason,
    RefundStatus,
    RefundType,
    ToolExecutionStatus,
    VerificationStatus,
)
from app.models.task import Task
from app.models.tool_execution import ToolExecution
from app.repositories.approvals import ApprovalRepository
from app.repositories.audit import AuditRepository
from app.repositories.commerce import CommerceRepository
from app.repositories.tasks import TaskRepository
from app.repositories.tool_executions import ToolExecutionRepository
from app.services.approvals import ApprovalService, canonical_digest
from app.services.outbox import OutboxService
from app.services.refund_policy import RefundPolicy
from app.services.verification import VerificationService

# 退款原因 / 退款类型的中文唯一名（口径 §1.1、§1.2）。
#
# 唯一一处：服务端给，前端只显示服务端给的这一份，前端不复制这张表。
REFUND_REASON_LABELS: dict[RefundReason, str] = {
    RefundReason.QUALITY: "质量问题",
    RefundReason.DAMAGED: "破损",
    RefundReason.WRONG_ITEM: "发错商品",
    RefundReason.NOT_AS_DESCRIBED: "与描述不符",
    RefundReason.NO_LONGER_NEEDED: "不再需要",
    RefundReason.OTHER: "其他",
}
REFUND_TYPE_LABELS: dict[RefundType, str] = {
    RefundType.FULL: "全额退款",
    RefundType.PARTIAL: "部分退款",
    RefundType.SHIPPING: "运费退款",
    RefundType.PRICE_PROTECTION: "价保退款",
}
# 「未提供」是显示标签，不是枚举成员：它不进数据库、不进摘要（口径 §1 术语表）。
NOT_PROVIDED_LABEL = "未提供"


def refund_reason_label(refund_reason: RefundReason | None) -> str:
    """退款原因的中文唯一名。缺值是「未提供」，绝不变成 OTHER。"""
    if refund_reason is None:
        return NOT_PROVIDED_LABEL
    return REFUND_REASON_LABELS[refund_reason]


def refund_type_label(refund_type: RefundType | None) -> str:
    """退款类型的中文唯一名。缺值是「未提供」，绝不变成 FULL。"""
    if refund_type is None:
        return NOT_PROVIDED_LABEL
    return REFUND_TYPE_LABELS[refund_type]


def refund_reason_of(payload: dict[str, Any]) -> RefundReason | None:
    """载荷里的退款原因；没有就是没有，读不懂也是没有，绝不兜底 OTHER。

    存量载荷没有 ``reason`` 键（口径 §2「在途审批安全」），读出来必须是空，
    不能替它选一个可能错的缘由。
    """
    raw = payload.get("reason")
    if raw is None:
        return None
    try:
        return RefundReason(str(raw))
    except ValueError:
        return None


def refund_type_of(payload: dict[str, Any]) -> RefundType | None:
    """载荷里的退款类型；没有就是没有，绝不默认 FULL（口径 §5.6.6）。"""
    raw = payload.get("refund_type")
    if raw is None:
        return None
    try:
        return RefundType(str(raw))
    except ValueError:
        return None


# 档位快照的四字段，逐字段比对用（B-3 口径 §5.3；键形见其 §5.1）。
APPROVAL_TIER_CHANGED = "APPROVAL_TIER_CHANGED"
_TIER_SNAPSHOT_KEYS = ("tier_id", "max_cents", "roles", "required_approvals")


def approval_tier_drifted(payload: dict[str, Any], amount_cents: int) -> bool:
    """执行前的显式档位重校验：当前档位表解析结果与存量快照是否已不一致。

    - 载荷**不含** ``approval_tier`` 键（存量审批）→ 跳过，返回假。
      理由见 B-3 口径 §5.3：跳过才不会误伤在途审批（不 retro-fit）。
    - 含该键 → 用当前表对该金额重解析，四字段逐字段比对；任一不同即档位
      规格已变（快照在装载期已规范化，所以比对是逐字比对）。快照形状读不出
      （非 dict）按「不一致」处理：执行的就是不被信任的规格。
    - 阈值（``refund_approval_threshold_cents``）不参与这条判定：它回答
      「要不要人批」，与「按什么规格批」是两个问题（口径 §3）。
    """
    stored = payload.get("approval_tier")
    if stored is None:
        return False
    current = get_approval_tiers().tier_for(amount_cents).snapshot()
    if not isinstance(stored, dict):
        return True
    return any(stored.get(key) != current[key] for key in _TIER_SNAPSHOT_KEYS)


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
    # 退款原因 / 退款类型：执行时从审批载荷读出来，回执与展示据此给中文唯一名。
    refund_reason: RefundReason | None = None
    refund_type: RefundType | None = None


class MockRefundProvider:
    """Idempotent mock provider with injectable failure modes."""

    def __init__(self, session: Session, *, mode: str, delay_seconds: float = 0.0) -> None:
        self.repo = CommerceRepository(session)
        self.mode = mode
        self.delay_seconds = delay_seconds

    def query(self, idempotency_key: str) -> Refund | None:
        """按幂等键问提供方要那一笔退款记录；没有就是没有，不兜底成任何状态。"""
        return self.repo.get_refund_by_idempotency(idempotency_key)

    def request(
        self,
        order: Order,
        amount_cents: int,
        idempotency_key: str,
        *,
        refund_reason: RefundReason | None = None,
        refund_type: RefundType | None = None,
        reason_note: str | None = None,
    ) -> Refund:
        """发起一笔退款：先按幂等键复用已有记录，再按注入的失败形态失败或成功。"""
        existing = self.query(idempotency_key)
        if existing is not None:
            return existing
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)

        if self.mode == "fail":
            self._create(
                order,
                amount_cents,
                idempotency_key,
                RefundStatus.FAILED,
                refund_reason=refund_reason,
                refund_type=refund_type,
                reason_note=reason_note,
            )
            raise ProviderFailure("Mock provider rejected the refund.")
        if self.mode == "timeout_before":
            raise ProviderTimeout("Mock provider timed out before committing.")
        if self.mode == "unknown":
            raise ProviderUnknown("Mock provider returned an unknown status.")

        refund = self._create(
            order,
            amount_cents,
            idempotency_key,
            RefundStatus.SUCCEEDED,
            refund_reason=refund_reason,
            refund_type=refund_type,
            reason_note=reason_note,
        )
        if self.mode == "timeout_after":
            raise ProviderTimeout("Mock provider committed but the response timed out.")
        return refund

    def _create(
        self,
        order: Order,
        amount_cents: int,
        idempotency_key: str,
        status: RefundStatus,
        *,
        refund_reason: RefundReason | None = None,
        refund_type: RefundType | None = None,
        reason_note: str | None = None,
    ) -> Refund:
        token = uuid.uuid4().hex[:10]
        refund = Refund(
            id=f"rfd_{token}",
            order_id=order.id,
            tenant_id=order.tenant_id,
            user_id=order.user_id,
            amount_cents=amount_cents,
            status=status,
            reason=refund_reason,
            refund_type=refund_type,
            reason_note=reason_note,
            external_refund_id=f"ext_{token}",
            idempotency_key=idempotency_key,
        )
        return self.repo.create_refund(refund)


# 自动执行路径的公开回执措辞（B-3 口径 §4.3）：客户要能看出这笔是自动处理的，
# 不是人批过的；「已审批通过」类措辞不得被自动路径复用。只在执行成功时加缀：
# 失败与转人工分支的回执两路径逐字相同（§8 第 12 条）。
AUTO_RECEIPT_PREFIX = "已按规则自动处理，"


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
        """审批路径的绑定包装器（B-3 口径 §4.2.1）：只做审批行绑定。

        权威校验全在 ``_execute_refund``。
        先核对「审批属于本任务」「台账还在等这份审批」，再带着审批行里的
        载荷进入唯一执行体；审批行失效在自动路径不存在，由执行体按
        ``approval is None`` 分支处理。
        """
        task = self.tasks.require(task_id)
        approval = self.approvals.require(approval_id)
        if approval.task_id != task_id:
            raise NotFoundError("Approval not found for this task.")
        ledger = self.executions.get_by_idempotency(task_id, f"refund:{task_id}")
        if (
            ledger is not None
            and ledger.status is ToolExecutionStatus.PENDING
            and approval.status is not ApprovalStatus.APPROVED
        ):
            raise ConflictError(f"Approval is {approval.status.value}, not APPROVED.")
        payload = dict(approval.action_payload or {})
        return self._execute_refund(task=task, ledger=ledger, payload=payload, approval=approval)

    def execute_auto(self, task_id: str) -> RefundOutcome:
        """自动执行入口（B-3 口径 §4.2）：没有审批行，绑定依据是幂等台账。

        与审批路径共用同一个权威执行体 ``_execute_refund``；绑定校验换成
        台账等价物（载荷重算比对 ``request_digest`` + 订单当前版本比对），
        强度对照审批行的 ``verify_binding``，两条不变量都保留（口径 §4.2.1）。
        """
        task = self.tasks.require(task_id)
        ledger = self.executions.get_by_idempotency(task_id, f"refund:{task_id}")
        payload = dict(ledger.request_payload or {}) if ledger is not None else {}
        outcome = self._execute_refund(task=task, ledger=ledger, payload=payload, approval=None)
        if outcome.status is VerificationStatus.VERIFIED_SUCCESS:
            outcome = replace(outcome, summary=f"{AUTO_RECEIPT_PREFIX}{outcome.summary}")
        return outcome

    def _execute_refund(
        self,
        *,
        task: Task,
        ledger: ToolExecution | None,
        payload: dict[str, Any],
        approval: Approval | None,
    ) -> RefundOutcome:
        """唯一的权威执行体：订单加锁、并发阻断、资格复核、绑定校验、
        渠道调用、对账确认，两条路径同强度、同落库（B-3 口径 §4.2.1）。

        ``approval`` 为 ``None`` 表示自动路径：没有审批行可失效或消费，档位
        重校验（§5.3 只约束在途审批）也自然跳过；其余分支逐字同源。
        """
        task_id = task.id
        amount = int(payload.get("amount_cents", 0))
        order_id = str(payload.get("order_id", ""))
        # 原因与类型取自被摘要钉死的那份载荷；补充说明取自任务上下文，
        # 它不在载荷里，因此也不在摘要里（口径 §4.2）。
        refund_reason = refund_reason_of(payload)
        refund_type = refund_type_of(payload)
        reason_note = self._reason_note(task, refund_reason)

        if not order_id or ledger is None:
            return RefundOutcome(
                VerificationStatus.NEEDS_HUMAN_REVIEW,
                "退款申请数据不完整，已转人工复核，未执行退款。",
                reason_code="MISSING_EXECUTION_CONTEXT",
                refund_reason=refund_reason,
                refund_type=refund_type,
            )

        if ledger.status is ToolExecutionStatus.RECONCILED:
            order = self.commerce.get_order_for_update(order_id)
            if order is None:
                return RefundOutcome(
                    VerificationStatus.NEEDS_HUMAN_REVIEW,
                    "退款申请数据不完整，已转人工复核，未执行退款。",
                    reason_code="MISSING_EXECUTION_CONTEXT",
                    refund_reason=refund_reason,
                    refund_type=refund_type,
                )
            refund = self.provider.query(ledger.idempotency_key)
            return self._finalize(
                ledger,
                refund,
                order,
                amount,
                already_reconciled=True,
                approval=approval,
                refund_reason=refund_reason,
                refund_type=refund_type,
            )

        if ledger.status in {ToolExecutionStatus.SENT, ToolExecutionStatus.UNKNOWN}:
            return self.reconcile_task(task_id)

        order = self.commerce.get_order_for_update(order_id)
        if order is None:
            return RefundOutcome(
                VerificationStatus.NEEDS_HUMAN_REVIEW,
                "退款申请数据不完整，已转人工复核，未执行退款。",
                reason_code="MISSING_EXECUTION_CONTEXT",
                refund_reason=refund_reason,
                refund_type=refund_type,
            )

        foreign = self.commerce.blocking_refund_except(order.id, ledger.idempotency_key)
        if foreign is not None:
            self._reject(
                approval,
                ledger,
                reason="ORDER_REFUND_IN_PROGRESS",
                message=f"another refund exists for this order: {foreign.id}",
            )
            return RefundOutcome(
                VerificationStatus.NEEDS_HUMAN_REVIEW,
                "该订单已有退款正在处理或已经成功，本次未重复执行；已转人工复核。",
                ledger_id=ledger.id,
                reason_code="ORDER_REFUND_IN_PROGRESS",
                refund_reason=refund_reason,
                refund_type=refund_type,
            )

        # 绑定校验两条都保留（口径 §4.2.1）：审批路径核审批行
        # （载荷替换 resource_version 后重算 digest 比对 action_digest），
        # 自动路径核幂等台账（载荷重算比对 request_digest + 版本比对）。
        if approval is not None:
            bound, reason = self.approval_service.verify_binding(approval, order)
            binding_summary = "审批绑定的订单版本已变化，旧审批失效，未执行退款；已转人工复核。"
        else:
            bound, reason = self._verify_ledger_binding(ledger, payload, order)
            binding_summary = "执行台账与当前订单或摘要不一致，本次未执行退款；已转人工复核。"
        if not bound:
            self._reject(approval, ledger, reason=reason)
            self.audit.record(
                actor_id=(approval.reviewer_id or "system") if approval is not None else "system",
                tenant_id=order.tenant_id,
                action="refund.invalidated",
                resource_type="order",
                resource_id=order.id,
                outcome="REJECTED",
                detail={"reason": reason, "task_id": task_id},
            )
            # 拒绝审计当场落库：直接调用执行体的路径（自动入口）没有后续的
            # 运行时提交，不补这一刀审计就会被回滚掉，失败变得不可观察。
            # 审批路径由 approve 收尾提交，这里提前提交只改变落库时机，
            # 不改变既有读数（§4.2.1 不变量 5）。
            self.session.commit()
            return RefundOutcome(
                VerificationStatus.NEEDS_HUMAN_REVIEW,
                binding_summary,
                ledger_id=ledger.id,
                reason_code=reason,
                refund_reason=refund_reason,
                refund_type=refund_type,
            )

        # 档位重校验（B-3 口径 §5.3）：只约束**在途审批行**——处理形状照上面的
        # verify_binding 失败分支：失效、台账取消、留审计、转人工复核，退款不执行。
        # 载荷没有 approval_tier 键的存量审批在 approval_tier_drifted 里被跳过，不误伤。
        # 自动路径没有审批行（§4.2 第 1 条），这条重校验按定义不适用：§5.3 失效的是
        # 「人已批过的那份规格」，自动路径没有人批过任何规格；台账载荷里的档位快照
        # 已由上面的 request_digest 重算覆盖，篡改即检出。
        if approval is not None and approval_tier_drifted(payload, amount):
            self.approval_service.invalidate(approval, reason=APPROVAL_TIER_CHANGED)
            ledger.status = ToolExecutionStatus.CANCELLED
            ledger.error_code = APPROVAL_TIER_CHANGED
            ledger.error_message = (
                "the tier resolved by the current table differs from the approval snapshot"
            )
            self.session.commit()
            self.audit.record(
                actor_id=approval.reviewer_id or "system",
                tenant_id=order.tenant_id,
                action="refund.invalidated",
                resource_type="order",
                resource_id=order.id,
                outcome="REJECTED",
                detail={"reason": APPROVAL_TIER_CHANGED, "task_id": task_id},
            )
            return RefundOutcome(
                VerificationStatus.NEEDS_HUMAN_REVIEW,
                "审批档位已变化，旧审批失效，未执行退款；已转人工复核。",
                ledger_id=ledger.id,
                reason_code=APPROVAL_TIER_CHANGED,
                refund_reason=refund_reason,
                refund_type=refund_type,
            )

        identity = Identity(tenant_id=task.tenant_id, user_id=task.user_id, roles=frozenset())
        eligibility = self.policy.check(identity, order.id, amount)
        if not eligibility.eligible:
            self._reject(approval, ledger, reason="ELIGIBILITY_CHANGED")
            ledger.status = ToolExecutionStatus.CANCELLED
            ledger.error_code = "ELIGIBILITY_CHANGED"
            self.session.commit()
            return RefundOutcome(
                VerificationStatus.NEEDS_HUMAN_REVIEW,
                "审批后复核发现退款资格已变化，未执行退款；已转人工复核。",
                ledger_id=ledger.id,
                reason_code="ELIGIBILITY_CHANGED",
                refund_reason=refund_reason,
                refund_type=refund_type,
            )

        if not self.executions.claim_for_send(ledger.id):
            self.session.rollback()
            return self.reconcile_task(task_id)
        self.session.commit()

        try:
            refund = self.provider.request(
                order,
                amount,
                ledger.idempotency_key,
                refund_reason=refund_reason,
                refund_type=refund_type,
                reason_note=reason_note,
            )
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
                refund_reason=refund_reason,
                refund_type=refund_type,
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
                refund_reason=refund_reason,
                refund_type=refund_type,
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
            refund_reason=refund_reason,
            refund_type=refund_type,
        )

    def reconcile_task(self, task_id: str) -> RefundOutcome:
        """拿台账去问权威记录：这笔退款到底成没成，成不了就给出下一步。

        只读一次提供方、只写「确认到了」这一种状态；查不到就挂
        ``PENDING_CONFIRMATION`` 并排一次对账，绝不把未知判成失败。
        """
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
        # 原因与类型仍取自被摘要钉死的那一份快照：对账不重新推断（口径 §4.2 第 6 条）。
        # 审批路径读审批载荷；自动执行路径没有审批行，其台账 request_payload 与审批
        # 载荷由同一次 refund_payload 产生（app/services/runtime.py:752-766），是同一份快照。
        if approval is not None:
            snapshot_payload = dict(approval.action_payload or {})
        else:
            snapshot_payload = dict(ledger.request_payload or {})
        return self._finalize(
            ledger,
            refund,
            order,
            amount,
            already_reconciled=False,
            approval=approval,
            refund_reason=refund_reason_of(snapshot_payload),
            refund_type=refund_type_of(snapshot_payload),
        )

    def _reject(
        self,
        approval: Approval | None,
        ledger: ToolExecution,
        *,
        reason: str,
        message: str | None = None,
    ) -> None:
        """统一的「执行前拒绝」落库（B-3 口径 §4.2.1 不变量 4）。

        两条路径共用这一个形状：审批路径把审批行失效（既有行为逐字保留），
        自动路径没有审批行可失效，改为只落台账的取消态与错误码——这正是
        口径规定的「自动路径没有审批行可失效，改为写审计与台账错误码」。
        审计是否补记由调用方按分支决定（并发冲突与资格复核分支今天就不写
        ``refund.invalidated`` 审计，两路径保持一致，§8 第 12 条）。
        """
        if approval is not None:
            self.approval_service.invalidate(approval, reason=reason)
        ledger.status = ToolExecutionStatus.CANCELLED
        ledger.error_code = reason
        if message is not None:
            ledger.error_message = message
        self.session.commit()

    def _verify_ledger_binding(
        self, ledger: ToolExecution, payload: dict[str, Any], order: Order
    ) -> tuple[bool, str]:
        """自动路径的绑定校验（B-3 口径 §4.2 第 6 条）：执行的就是台账里那一件。

        强度对照审批行的 ``verify_binding``（§4.2.1 不变量 3：两份等价校验
        都保留，不是二选一）：

        1. 载荷重算 digest 与台账 ``request_digest`` 常量时间比对——载荷被
           改写而摘要没跟上，即为 ``EXECUTION_DIGEST_MISMATCH``；
        2. 载荷里的 ``resource_version`` 与订单当前版本比对——与审批路径
           「替换版本后重算」检出的是同一件事（``RESOURCE_VERSION_CHANGED``）。
        """
        if not secrets.compare_digest(canonical_digest(payload), ledger.request_digest):
            return False, "EXECUTION_DIGEST_MISMATCH"
        if int(payload.get("resource_version", -1)) != order.version:
            return False, "RESOURCE_VERSION_CHANGED"
        return True, "OK"

    @staticmethod
    def _reason_note(task: Task, refund_reason: RefundReason | None) -> str | None:
        """补充说明：只有一个来源，且只给「其他」写。

        来源是任务上下文里的 ``reason_note``（口径 §4.2：它不进载荷、不进摘要）；
        原因不是「其他」时不写（口径 §2：只有 OTHER 才有补充说明）。
        """
        if refund_reason is not RefundReason.OTHER:
            return None
        context = getattr(task, "context", None)
        if not isinstance(context, dict):
            return None
        raw = context.get("reason_note")
        if raw is None:
            return None
        note = str(raw).strip()
        if not note:
            return None
        return note

    def _finalize(
        self,
        ledger: ToolExecution,
        refund: Refund | None,
        order: Order,
        amount_cents: int,
        *,
        already_reconciled: bool,
        approval: Approval | None = None,
        refund_reason: RefundReason | None = None,
        refund_type: RefundType | None = None,
    ) -> RefundOutcome:
        if refund is None:
            return RefundOutcome(
                VerificationStatus.PENDING_CONFIRMATION,
                "对账暂未找到退款记录，结果仍未确认；请勿重复提交。",
                ledger_id=ledger.id,
                reason_code="NOT_FOUND_YET",
                refund_reason=refund_reason,
                refund_type=refund_type,
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
            refund_reason=refund_reason,
            refund_type=refund_type,
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
