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
from app.models.enums import ApprovalStatus
from app.models.task import Task
from app.repositories.approvals import ApprovalRepository
from app.repositories.audit import AuditRepository


def canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def refund_payload(order: Order, amount_cents: int, policy_version: str) -> dict[str, Any]:
    return {
        "action": "REFUND",
        "order_id": order.id,
        "amount_cents": amount_cents,
        "currency": order.currency,
        "resource_version": order.version,
        "policy_version": policy_version,
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
    ) -> Approval:
        payload = refund_payload(order, amount_cents, policy_version)
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
