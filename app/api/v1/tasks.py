from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_runtime
from app.core.security import Identity, get_identity
from app.schemas.tasks import (
    ApprovalDecisionRequest,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
)
from app.services.runtime import TaskRuntime

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskCreateRequest,
    identity: Identity = Depends(get_identity),
    runtime: TaskRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    return runtime.create_task(identity, body.request, body.context)


@router.get("", response_model=TaskListResponse)
def list_tasks(
    identity: Identity = Depends(get_identity),
    runtime: TaskRuntime = Depends(get_runtime),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return runtime.list_tasks(identity, limit=limit, offset=offset)


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: str,
    identity: Identity = Depends(get_identity),
    runtime: TaskRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    return runtime.get_task(identity, task_id)


@router.post("/{task_id}/approve", response_model=TaskResponse)
def approve_task(
    task_id: str,
    body: ApprovalDecisionRequest,
    identity: Identity = Depends(get_identity),
    runtime: TaskRuntime = Depends(get_runtime),
) -> dict[str, Any]:
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
    return runtime.cancel(identity, task_id)


@router.get("/{task_id}/events")
def task_events(
    task_id: str,
    identity: Identity = Depends(get_identity),
    runtime: TaskRuntime = Depends(get_runtime),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    events = runtime.events(identity, task_id, after_id=last_event_id)

    def stream() -> Iterator[str]:
        for event in events:
            payload = json.dumps(
                {**event["data"], "created_at": event["created_at"]},
                ensure_ascii=False,
            )
            yield f"id: {event['id']}\nevent: {event['event']}\ndata: {payload}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
