from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
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
