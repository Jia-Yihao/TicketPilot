"""The seat view of the outbox.

A message in the queue is a debt the request path could not settle. The
queue shows what is waiting, what has already landed and what became a
dead letter, so a refund nobody can explain has somewhere to be looked
up. Staff-only and tenant-scoped, like every other seat API.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_runtime, get_session
from app.core.errors import ForbiddenError, ValidationAppError
from app.core.security import Identity, get_identity
from app.models.enums import OutboxStatus
from app.repositories.outbox import OutboxRepository
from app.schemas.outbox import (
    OutboxDeliveryResponse,
    OutboxMessageSummary,
    OutboxQueueResponse,
)
from app.services.runtime import TaskRuntime

router = APIRouter(prefix="/api/v1/outbox", tags=["outbox"])


def _require_seat(identity: Identity) -> None:
    if not identity.has_role("support") and not identity.has_role("approver"):
        raise ForbiddenError("Support or approver role required.")


def _parse_status(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return OutboxStatus(raw.upper()).value
    except ValueError as exc:
        raise ValidationAppError(
            "Unknown outbox status.",
            details={"allowed": [item.value for item in OutboxStatus]},
        ) from exc


def _summary(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "aggregate_type": row.aggregate_type,
        "aggregate_id": row.aggregate_id,
        "event_type": row.event_type,
        "status": row.status,
        "retry_count": row.retry_count,
        "last_error": row.last_error,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
    }


@router.get("", response_model=OutboxQueueResponse)
def list_outbox(
    status: str | None = Query(default=None, max_length=16),
    event_type: str | None = Query(default=None, max_length=64),
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """列本租户外发队列，可按状态与事件类型过滤；需要 support 或 approver 角色。"""
    _require_seat(identity)
    repo = OutboxRepository(session)
    rows = repo.list_for(
        identity,
        status=_parse_status(status),
        event_type=event_type,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [OutboxMessageSummary(**_summary(row)) for row in rows],
        "total": repo.count_for(identity, status=_parse_status(status)),
        "limit": limit,
        "offset": offset,
    }


@router.post("/deliver", response_model=OutboxDeliveryResponse)
def deliver_outbox(
    identity: Identity = Depends(get_identity),
    runtime: TaskRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    """Run one delivery pass.

    The seat asks for it when the queue should not wait for the next
    scheduled pass of the worker.
    """
    _require_seat(identity)
    report = runtime.deliver_outbox()
    return {
        "claimed": report.claimed,
        "sent": report.sent,
        "failed": report.failed,
        "dead_lettered": report.dead_lettered,
    }
