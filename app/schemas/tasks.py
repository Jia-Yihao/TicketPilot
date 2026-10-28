from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    """新建任务的请求体。

    ``context`` 是自由结构化槽位。退款相关的键有两个（口径 §5.6.3、§5.6.6）：
    ``refund_reason``（``RefundReason`` 值域；缺值或取值非法一律 422）与
    ``refund_type``（``RefundType`` 值域；缺值不由调用方补，服务端按订单可退
    余额派生）。``reason_note`` 只在 ``refund_reason`` 为 ``OTHER`` 时可提交。
    """

    request: str = Field(min_length=1, max_length=5000)
    context: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecisionRequest(BaseModel):
    approval_id: str | None = None
    decision: Literal["APPROVE", "REJECT"]
    reason: str | None = Field(default=None, max_length=500)


class PendingApproval(BaseModel):
    approval_id: str
    action_type: str
    status: str
    expires_at: str
    amount_cents: int | None = None


class TaskListItem(BaseModel):
    task_id: str
    status: str
    request: str
    outcome: str | None = None
    summary: str | None = None
    created_at: str
    updated_at: str


class TaskListResponse(BaseModel):
    items: list[TaskListItem]
    total: int
    limit: int
    offset: int


class TaskResponse(BaseModel):
    task_id: str
    status: str
    request: str
    context: dict[str, Any]
    result: dict[str, Any] | None
    version: int
    trace_id: str | None = None
    created_at: str
    updated_at: str
    pending_approval: PendingApproval | None = None
    latest_approval: dict[str, Any] | None = None
    tool_execution: dict[str, Any] | None = None
    # True when the task was returned by an Idempotency-Key replay rather
    # than created by this request.
    idempotent_replay: bool = False
