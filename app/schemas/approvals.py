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
    # 审批的决定 / 驳回 / 失效原因，与退款原因不是同一个概念（B-1 口径 §1.0）。
    reason: str | None = None
    # 退款原因 / 退款类型：枚举值 + 服务端给的中文唯一名；缺值显示「未提供」。
    refund_reason: str | None = None
    refund_reason_label: str | None = None
    refund_type: str | None = None
    refund_type_label: str | None = None
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
    # 补充说明只在坐席 / 审批详情侧可见：不进载荷、不进摘要、不进公开回执。
    refund_reason_note: str | None = None


class ApprovalListResponse(BaseModel):
    items: list[ApprovalListItem]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]


class ApprovalDetailResponse(ApprovalDetail):
    pass
