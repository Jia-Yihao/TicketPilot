from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import (
    ApprovalExpiredError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.core.security import Identity, require_approver
from app.core.time import utcnow, utcnow_plus
from app.models.approval import Approval
from app.models.commerce import Order
from app.models.enums import ApprovalStatus, RefundReason, RefundType
from app.models.task import Task
from app.repositories.approvals import ApprovalRepository
from app.repositories.audit import AuditRepository


def canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def refund_payload(
    order: Order,
    amount_cents: int,
    policy_version: str,
    *,
    refund_reason: RefundReason,
    refund_type: RefundType,
    approval_tier: dict[str, Any],
) -> dict[str, Any]:
    """退款审批快照：键集合定死为九键，``reason`` / ``refund_type`` / ``approval_tier`` 必填。

    形参用带上下文的 ``refund_reason``（链路命名，§5.6.4），映射为口径裸名
    ``reason`` 只发生在这一处边界上。摘要覆盖这两个键，因此同一订单两条不同
    原因的退款摘要不同，新旧审批互不可替换。

    ``approval_tier`` 是**整个档位快照**（``tier_id`` / ``max_cents`` / ``roles``
    / ``required_approvals``，B-3 口径 §5.1）：只放 ``tier_id`` 的话改 ``roles``
    或 ``required_approvals`` 摘要不变，「分层变化会让旧审批失效」就落空了。
    快照由调用方从 ``ApprovalTier.snapshot()`` 取（roles 已在装载期按字典序
    规范化，§2.3 校验 5），是 B-1 口径 §4.1 八键清单经 B-3 口径 §5.2 修订后的
    第九键。
    """
    return {
        "action": "REFUND",
        "order_id": order.id,
        "amount_cents": amount_cents,
        "currency": order.currency,
        "resource_version": order.version,
        "policy_version": policy_version,
        "reason": refund_reason.value,
        "refund_type": refund_type.value,
        "approval_tier": approval_tier,
    }


class ApprovalService:
    def __init__(self, session: Session, *, ttl_minutes: int = 30) -> None:
        self.session = session
        self.repo = ApprovalRepository(session)
        self.audit = AuditRepository(session)
        self.ttl_minutes = ttl_minutes

    def create_refund_approval(
        self,
        *,
        task_id: str,
        order: Order,
        amount_cents: int,
        requested_by: str,
        policy_version: str,
        refund_reason: RefundReason,
        refund_type: RefundType,
        approval_tier: dict[str, Any],
    ) -> Approval:
        payload = refund_payload(
            order,
            amount_cents,
            policy_version,
            refund_reason=refund_reason,
            refund_type=refund_type,
            approval_tier=approval_tier,
        )
        approval = Approval(
            id=f"apr_{uuid.uuid4().hex[:16]}",
            task_id=task_id,
            action_type="REFUND",
            resource_type="order",
            resource_id=order.id,
            action_digest=canonical_digest(payload),
            resource_version=order.version,
            status=ApprovalStatus.PENDING,
            requested_by=requested_by,
            action_payload=payload,
            expires_at=utcnow_plus(self.ttl_minutes),
        )
        self.repo.create(approval)
        self.audit.record(
            actor_id=requested_by,
            tenant_id=order.tenant_id,
            action="approval.request",
            resource_type="approval",
            resource_id=approval.id,
            outcome="PENDING",
            detail={"task_id": task_id, "order_id": order.id, "amount_cents": amount_cents},
        )
        return approval

    def decide(
        self,
        *,
        task_id: str,
        approval_id: str,
        decision: str,
        reviewer: Identity,
        reason: str | None = None,
    ) -> Approval:
        require_approver(reviewer)
        approval = self.repo.require_for_update(approval_id)
        if approval.task_id != task_id:
            raise NotFoundError("Approval not found for this task.")

        task = self.session.get(Task, approval.task_id)
        if task is None or task.tenant_id != reviewer.tenant_id:
            raise NotFoundError("Approval not found.")
        if approval.status is not ApprovalStatus.PENDING:
            raise ConflictError(f"Approval already resolved as {approval.status.value}.")
        if utcnow() >= approval.expires_at:
            self.repo.resolve(
                approval, ApprovalStatus.EXPIRED, reviewer_id=reviewer.user_id, reason="expired"
            )
            raise ApprovalExpiredError("Approval has expired; request a new one.")
        if reviewer.user_id == approval.requested_by:
            raise ForbiddenError("Self-approval is not allowed.")
        self._require_tier_role(approval, reviewer)

        status = ApprovalStatus.APPROVED if decision == "APPROVE" else ApprovalStatus.REJECTED
        self.repo.resolve(approval, status, reviewer_id=reviewer.user_id, reason=reason)
        self.audit.record(
            actor_id=reviewer.user_id,
            tenant_id=task.tenant_id,
            action="approval.decide",
            resource_type="approval",
            resource_id=approval.id,
            outcome=status.value,
            detail={"task_id": task_id, "reason": reason or ""},
        )
        return approval

    @staticmethod
    def _require_tier_role(approval: Approval, reviewer: Identity) -> None:
        """审批人必须持有该档位 ``roles`` 中的至少一个角色（B-3 口径 §4.1）。

        判定走既有的 ``Identity.has_role``，因此默认档位 ``roles == ["approver"]``
        时这条校验与今天逐字等价（admin 的既有兼容语义保持不变）。

        载荷不含 ``approval_tier`` 键的存量审批不受本条约束——与 §5.3 的
        「在途审批不 retro-fit」同一条理由：键不存在就没有档位可比。
        档位快照坏了（roles 缺失或为空）不静默放行：那是数据被改坏的信号，
        报错比让一个不知道归谁批的档位被批掉更可观察。
        """
        payload = dict(approval.action_payload or {})
        tier = payload.get("approval_tier")
        if tier is None:
            return
        roles = tier.get("roles") if isinstance(tier, dict) else None
        if not isinstance(roles, list) or not roles:
            raise ConflictError("Approval tier snapshot has no usable roles.")
        if not any(reviewer.has_role(str(role)) for role in roles):
            raise ForbiddenError(
                "Reviewer does not hold a role allowed for this approval tier."
            )

    def verify_binding(self, approval: Approval, order: Order) -> tuple[bool, str]:
        payload = dict(approval.action_payload or {})
        if payload.get("order_id") != order.id:
            return False, "ORDER_MISMATCH"
        current = dict(payload)
        current["resource_version"] = order.version
        if not secrets.compare_digest(canonical_digest(current), approval.action_digest):
            return False, "RESOURCE_VERSION_CHANGED"
        return True, "OK"

    def invalidate(self, approval: Approval, *, reason: str) -> None:
        if approval.status is ApprovalStatus.PENDING:
            self.repo.resolve(approval, ApprovalStatus.INVALID, reason=reason)
        elif approval.status is ApprovalStatus.APPROVED:
            approval.status = ApprovalStatus.INVALID
            approval.reason = reason
            approval.resolved_at = utcnow()
