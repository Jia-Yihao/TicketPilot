from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.core.errors import ForbiddenError, NotFoundError
from app.core.security import Identity, get_identity
from app.core.time import utcnow
from app.models.conversation import Conversation
from app.models.observability import TraceSpan
from app.models.task import Task
from app.repositories.traces import TraceRepository

router = APIRouter(prefix="/api/v1/traces", tags=["traces"])

_STAFF_ROLES = ("approver", "support")

MAX_WINDOW_MINUTES = 60 * 24 * 90


def _require_staff(identity: Identity) -> None:
    if identity.has_role("admin") or any(
        identity.has_role(role) for role in _STAFF_ROLES
    ):
        return
    raise ForbiddenError("Staff role required.")


def _visible(
    identity: Identity, conversation_id: str | None, trace_id: str, session: Session
) -> bool:
    """Staff read any trace in the tenant; a customer reads only their own."""
    if identity.has_role("admin") or any(
        identity.has_role(role) for role in _STAFF_ROLES
    ):
        return True
    if conversation_id is not None:
        conversation = session.get(Conversation, conversation_id)
        if conversation is not None and conversation.user_id == identity.user_id:
            return True
    task = session.query(Task).filter(Task.trace_id == trace_id).first()
    return task is not None and task.user_id == identity.user_id


@router.get("/metrics")
def trace_metrics(
    window_minutes: int | None = Query(default=None, ge=1, le=MAX_WINDOW_MINUTES),
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Tenant-scoped span health for the operations dashboard.

    ``window_minutes`` scopes the measurement; without it the numbers cover
    the tenant's whole trace history. Rates are span-level, so one failed
    step inside a long trace is not averaged away.
    """
    _require_staff(identity)
    statement = select(
        TraceSpan.trace_id,
        TraceSpan.kind,
        TraceSpan.status,
        TraceSpan.latency_ms,
        TraceSpan.started_at,
    ).where(TraceSpan.tenant_id == identity.tenant_id)
    if window_minutes is not None:
        statement = statement.where(
            TraceSpan.started_at >= utcnow() - timedelta(minutes=window_minutes)
        )
    rows = session.execute(statement).all()

    total = len(rows)
    errors = sum(1 for row in rows if row.status != "OK")
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_hour: dict[str, int] = {}
    for row in rows:
        by_kind[row.kind] = by_kind.get(row.kind, 0) + 1
        by_status[row.status] = by_status.get(row.status, 0) + 1
        hour = f"{row.started_at.hour:02d}"
        by_hour[hour] = by_hour.get(hour, 0) + 1

    latencies = sorted(row.latency_ms for row in rows)
    p95 = 0
    if latencies:
        rank = max(0, min(len(latencies) - 1, math.ceil(0.95 * len(latencies)) - 1))
        p95 = latencies[rank]

    return {
        "window_minutes": window_minutes,
        "trace_count": len({row.trace_id for row in rows}),
        "spans": total,
        "error_spans": errors,
        "error_rate": round(errors / total, 4) if total else 0.0,
        "p95_latency_ms": p95,
        "by_kind": dict(sorted(by_kind.items())),
        "by_status": dict(sorted(by_status.items())),
        "by_hour": dict(sorted(by_hour.items())),
    }


@router.get("/{trace_id}")
def get_trace(
    trace_id: str,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """读一条 trace 的全部 span；staff 可读租户内任意一条，客户只读自己的。"""
    repository = TraceRepository(session)
    spans = repository.list_for_trace(trace_id)
    if not spans:
        raise NotFoundError("Trace not found.")
    conversation_id = spans[0].conversation_id
    if spans[0].tenant_id != identity.tenant_id:
        # Another tenant's trace must look exactly like a missing one.
        raise NotFoundError("Trace not found.")
    if not _visible(identity, conversation_id, trace_id, session):
        raise NotFoundError("Trace not found.")
    return {
        "trace_id": trace_id,
        "conversation_id": conversation_id,
        "spans": [
            {
                "span_id": span.id,
                "parent_span_id": span.parent_span_id,
                "kind": span.kind,
                "name": span.name,
                "status": span.status,
                "latency_ms": span.latency_ms,
                "attributes": span.attributes,
                "started_at": span.started_at.isoformat(),
                "ended_at": span.ended_at.isoformat(),
            }
            for span in spans
        ],
    }
