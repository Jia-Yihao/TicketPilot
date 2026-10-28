from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_runtime
from app.core.errors import ValidationAppError
from app.core.security import Identity, get_identity
from app.schemas.tasks import (
    ApprovalDecisionRequest,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
)
from app.services.runtime import TaskRuntime, resolve_refund_slots

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskCreateRequest,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", max_length=200
    ),
    identity: Identity = Depends(get_identity),
    runtime: TaskRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    """创建并执行一个任务；只处理调用者自己的请求，同一幂等键重放不会跑两次。"""
    # 退款申请必须有合法的退款原因：缺值或取值非法一律 422，不追问、不降级
    # （口径 §5.6.3）。判定与对话入口共用 resolve_refund_slots，两侧只翻译表现。
    slots = resolve_refund_slots(body.request, body.context)
    if not slots.ok:
        raise ValidationAppError(
            slots.error_message or "退款申请缺少合法的退款原因。",
            details={"refund_slot_error": slots.error_code},
        )
    return runtime.create_task(
        identity,
        body.request,
        body.context,
        idempotency_key=idempotency_key,
    )


@router.get("", response_model=TaskListResponse)
def list_tasks(
    identity: Identity = Depends(get_identity),
    runtime: TaskRuntime = Depends(get_runtime),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """列出调用者自己的任务；跨用户不可见，分页上限由查询参数拦。"""
    return runtime.list_tasks(identity, limit=limit, offset=offset)


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: str,
    identity: Identity = Depends(get_identity),
    runtime: TaskRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    """读一个任务；租户不符或非本人（且无审批 / 客服角色）一律 404。"""
    return runtime.get_task(identity, task_id)


@router.post("/{task_id}/approve", response_model=TaskResponse)
def approve_task(
    task_id: str,
    body: ApprovalDecisionRequest,
    identity: Identity = Depends(get_identity),
    runtime: TaskRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    """批准或驳回一条待审批退款；需要 approver 角色，通过后立即执行。"""
    return runtime.approve(
        identity,
        task_id,
        approval_id=body.approval_id,
        decision=body.decision,
        reason=body.reason,
    )


@router.post("/{task_id}/cancel", response_model=TaskResponse)
def cancel_task(
    task_id: str,
    identity: Identity = Depends(get_identity),
    runtime: TaskRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    """取消一个未终止的任务；调用者须是本人或持有审批 / 客服角色。"""
    return runtime.cancel(identity, task_id)


@router.get("/{task_id}/events")
def task_events(
    task_id: str,
    identity: Identity = Depends(get_identity),
    runtime: TaskRuntime = Depends(get_runtime),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    follow: bool = Query(default=True),
) -> StreamingResponse:
    """Stream a task's events, by default until the task reaches a terminal state.

    ``follow=false`` keeps the old shape: replay what is stored, then close.
    In follow mode the runtime polls the shared event table, so a stream opened
    against one worker still sees events written by another; ``Last-Event-ID``
    resumes without repeating what the client already has.
    """

    def frame(event: dict[str, Any]) -> str:
        payload = json.dumps(
            {**event["data"], "created_at": event["created_at"]},
            ensure_ascii=False,
        )
        return f"id: {event['id']}\nevent: {event['event']}\ndata: {payload}\n\n"

    def stream() -> Iterator[str]:
        source = (
            runtime.follow_events(identity, task_id, after_id=last_event_id)
            if follow
            else iter(runtime.events(identity, task_id, after_id=last_event_id))
        )
        for event in source:
            if event["id"] is None:
                # A heartbeat is an SSE comment, not a task event.
                yield ": ping\n\n"
                continue
            yield frame(event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
