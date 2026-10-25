from __future__ import annotations

from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.time import utcnow
from app.models.approval import Approval
from app.models.commerce import Order
from app.models.enums import ApprovalStatus
from app.models.task import Task

OPEN_STATUSES = (ApprovalStatus.PENDING, ApprovalStatus.APPROVED)


class ApprovalRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, approval_id: str) -> Approval | None:
        return self.session.get(Approval, approval_id)

    def require(self, approval_id: str) -> Approval:
        approval = self.get(approval_id)
        if approval is None:
            raise NotFoundError("Approval not found.")
        return approval

    def require_for_update(self, approval_id: str) -> Approval:
        stmt = (
            select(Approval)
            .where(Approval.id == approval_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        approval = self.session.scalars(stmt).first()
        if approval is None:
            raise NotFoundError("Approval not found.")
        return approval

    def open_for_resource(
        self, resource_type: str, resource_id: str, action_type: str
    ) -> Approval | None:
        stmt = select(Approval).where(
            Approval.resource_type == resource_type,
            Approval.resource_id == resource_id,
            Approval.action_type == action_type,
            Approval.status.in_(OPEN_STATUSES),
        )
        return self.session.scalars(stmt).first()

    def pending_for_task(self, task_id: str) -> Approval | None:
        stmt = select(Approval).where(
            Approval.task_id == task_id, Approval.status == ApprovalStatus.PENDING
        )
        return self.session.scalars(stmt).first()

    def latest_for_task(self, task_id: str) -> Approval | None:
        stmt = (
            select(Approval)
            .where(Approval.task_id == task_id)
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def create(self, approval: Approval) -> Approval:
        if self.pending_for_task(approval.task_id) is not None:
            raise ConflictError("Task already has a pending approval.")
        if (
            self.open_for_resource(
                approval.resource_type, approval.resource_id, approval.action_type
            )
            is not None
        ):
            raise ConflictError(
                "This resource already has an open approval.",
                code="APPROVAL_ALREADY_OPEN",
            )
        self.session.add(approval)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "This resource already has an open approval.",
                code="APPROVAL_ALREADY_OPEN",
            ) from exc
        return approval

    def consume(self, approval: Approval, *, reason: str) -> Approval:
        if approval.status is not ApprovalStatus.APPROVED:
            return approval
        approval.status = ApprovalStatus.CONSUMED
        if approval.reason is None:
            approval.reason = reason
        self.session.flush()
        return approval

    def resolve(
        self,
        approval: Approval,
        status: ApprovalStatus,
        *,
        reviewer_id: str | None = None,
        reason: str | None = None,
    ) -> Approval:
        if approval.status != ApprovalStatus.PENDING:
            raise ConflictError(f"Approval already resolved as {approval.status}.")
        approval.status = status
        approval.reviewer_id = reviewer_id
        approval.reason = reason
        approval.resolved_at = utcnow()
        self.session.flush()
        return approval

    def payload(self, approval: Approval) -> dict[str, Any]:
        return dict(approval.action_payload or {})

    def list_for_tenant(
        self,
        tenant_id: str,
        *,
        statuses: list[ApprovalStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[Approval, Task, Order | None]]:
        stmt = (
            select(Approval, Task, Order)
            .join(Task, Task.id == Approval.task_id)
            .outerjoin(
                Order,
                and_(Approval.resource_type == "order", Order.id == Approval.resource_id),
            )
            .where(Task.tenant_id == tenant_id)
            .order_by(
                case(
                    (Approval.status == ApprovalStatus.PENDING, 0),
                    (Approval.status == ApprovalStatus.APPROVED, 1),
                    else_=2,
                ),
                Approval.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        if statuses:
            stmt = stmt.where(Approval.status.in_(statuses))
        return [
            (approval, task, order)
            for approval, task, order in self.session.execute(stmt).all()
        ]

    def count_for_tenant(
        self,
        tenant_id: str,
        *,
        statuses: list[ApprovalStatus] | None = None,
    ) -> int:
        stmt = (
            select(func.count(Approval.id))
            .join(Task, Task.id == Approval.task_id)
            .where(Task.tenant_id == tenant_id)
        )
        if statuses:
            stmt = stmt.where(Approval.status.in_(statuses))
        return int(self.session.scalar(stmt) or 0)

    def counts_for_tenant(self, tenant_id: str) -> dict[str, int]:
        stmt = (
            select(Approval.status, func.count(Approval.id))
            .join(Task, Task.id == Approval.task_id)
            .where(Task.tenant_id == tenant_id)
            .group_by(Approval.status)
        )
        counts = {status.value: 0 for status in ApprovalStatus}
        for status, count in self.session.execute(stmt):
            counts[status.value] = int(count)
        return counts

    def get_for_tenant(
        self, approval_id: str, tenant_id: str
    ) -> tuple[Approval, Task, Order | None] | None:
        stmt = (
            select(Approval, Task, Order)
            .join(Task, Task.id == Approval.task_id)
            .outerjoin(
                Order,
                and_(Approval.resource_type == "order", Order.id == Approval.resource_id),
            )
            .where(Approval.id == approval_id, Task.tenant_id == tenant_id)
        )
        row = self.session.execute(stmt).first()
        if row is None:
            return None
        approval, task, order = row
        return approval, task, order
