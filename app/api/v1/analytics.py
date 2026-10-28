"""Read-only questions about the caller's own records.

Two doors into the same ledger: ``POST /query`` asks a question and gets an
answer, ``GET /queries`` shows what was asked before. A question that the
planner or the guard refuses comes back as ``SQL_REFUSED`` with the stable
reason code — the refusal is recorded too, so nothing disappears silently.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_analytics, get_session
from app.core.errors import ForbiddenError, NotFoundError, SqlRefusedError
from app.core.security import Identity, get_identity
from app.repositories.sql_queries import SqlQueryRepository
from app.schemas.analytics import (
    AnalyticsQueryListResponse,
    AnalyticsQueryRequest,
    AnalyticsQueryResponse,
    AnalyticsQuerySummary,
    LlmUsageResponse,
)
from app.services.llm_usage import MAX_WINDOW_HOURS, LlmUsageService
from app.sql.errors import SqlRejected
from app.sql.service import SqlAnalyticsService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

_STAFF_ROLES = ("approver", "support")


def _require_staff(identity: Identity) -> None:
    if identity.has_role("admin") or any(
        identity.has_role(role) for role in _STAFF_ROLES
    ):
        return
    raise ForbiddenError("Staff role required.")


@router.post("/query", response_model=AnalyticsQueryResponse)
def run_query(
    body: AnalyticsQueryRequest,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    analytics: SqlAnalyticsService = Depends(get_analytics),
) -> dict[str, Any]:
    """用一句自然语言问统计问题；只查调用者被允许的数据集，被拒也要入账。"""
    try:
        answer = analytics.answer(body.question, identity, session)
    except SqlRejected as exc:
        # The refusal is in the ledger too; keep it there.
        session.commit()
        raise SqlRefusedError(exc.reason, exc.detail) from exc
    session.commit()
    return {
        "query_id": answer.query_id,
        "question": answer.plan.question,
        "plan": {
            "dataset": answer.plan.dataset,
            "metric": answer.plan.metric,
            "group_by": answer.plan.group_by,
            "status": answer.plan.status,
            "time_range": (
                answer.plan.time_range.label if answer.plan.time_range else None
            ),
        },
        "route": answer.route,
        "sql": answer.sql,
        "columns": list(answer.columns),
        "rows": [dict(row) for row in answer.rows],
        "row_count": answer.row_count,
        "duration_ms": answer.duration_ms,
    }


@router.get("/llm-usage", response_model=LlmUsageResponse)
def llm_usage(
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    hours: int = Query(default=24, ge=1, le=MAX_WINDOW_HOURS),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """The tenant's model spend across every worker, newest first.

    Seat-only: the numbers name task and conversation ids, so a customer
    must not read them. A rules-only deployment returns an empty ledger
    rather than a 404 — "no spend" is a fact, not a missing resource.
    """
    _require_staff(identity)
    return LlmUsageService(session).summary(identity.tenant_id, hours=hours, limit=limit)


@router.get("/queries", response_model=AnalyticsQueryListResponse)
def list_queries(
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    dataset: str | None = Query(default=None, max_length=32),
    status: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """列调用者自己的查询台账，可按数据集与状态过滤。"""
    repository = SqlQueryRepository(session)
    rows = repository.list(
        identity, dataset=dataset, status=status, limit=limit, offset=offset
    )
    return {
        "items": [_summary(row) for row in rows],
        "total": repository.count(identity, dataset=dataset, status=status),
        "limit": limit,
        "offset": offset,
    }


@router.get("/queries/{query_id}", response_model=AnalyticsQuerySummary)
def get_query(
    query_id: str,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """读一条查询台账；别人的记录与不存在的记录返回同一种 404。"""
    row = SqlQueryRepository(session).get(identity, query_id)
    if row is None:
        # Another user's query must look exactly like a missing one.
        raise NotFoundError("Query not found.")
    return _summary(row)


def _route_of(row: Any) -> str:
    """The path a recorded query took, derived from what the ledger kept."""
    if getattr(row, "status", None) == "REJECTED":
        return "REFUSED"
    return "SQL" if row.sql_text else "PREFETCH"


def _summary(row: Any) -> dict[str, Any]:
    return {
        "query_id": row.id,
        "question": row.question,
        "dataset": row.dataset,
        "metric": row.metric,
        "group_by": row.group_by,
        "row_status": row.row_status,
        "time_range": row.time_range,
        "route": _route_of(row),
        "sql_text": row.sql_text,
        "row_count": row.row_count,
        "duration_ms": row.duration_ms,
        "status": row.status,
        "rejected_reason": row.rejected_reason,
        "trace_id": row.trace_id,
        "created_at": row.created_at.isoformat(),
    }
