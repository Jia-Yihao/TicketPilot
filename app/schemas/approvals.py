from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ApprovalOrderSummary(BaseModel):
    order_id: str
    user_id: str
    status: str
    total_cents: int
    refundable_cents: int
    currency: str
    version: int
    paid_at: str
    delivered_at: str | None = None


class ApprovalListItem(BaseModel):
    approval_id: str
    task_id: str
    status: str
    action_type: str
    amount_cents: int
    currency: str
    order_id: str
    requested_by: str
    reviewer_id: str | None = None
    reason: str | None = None
    resource_version: int
    created_at: str
    expires_at: str
    resolved_at: str | None = None
    request: str
    task_status: str
    task_outcome: str | None = None
    order: ApprovalOrderSummary | None = None


class ApprovalDetail(ApprovalListItem):
    action_digest: str
    action_payload: dict[str, Any]


class ApprovalListResponse(BaseModel):
    items: list[ApprovalListItem]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]


class ApprovalDetailResponse(ApprovalDetail):
    pass
