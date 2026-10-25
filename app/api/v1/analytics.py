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
from app.core.errors import NotFoundError, SqlRefusedError
from app.core.security import Identity, get_identity
from app.repositories.sql_queries import SqlQueryRepository
from app.schemas.analytics import (
    AnalyticsQueryListResponse,
    AnalyticsQueryRequest,
    AnalyticsQueryResponse,
    AnalyticsQuerySummary,
)
from app.sql.errors import SqlRejected
from app.sql.service import SqlAnalyticsService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.post("/query", response_model=AnalyticsQueryResponse)
def run_query(
    body: AnalyticsQueryRequest,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    analytics: SqlAnalyticsService = Depends(get_analytics),
) -> dict[str, Any]:
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
        "sql": answer.sql,
        "columns": list(answer.columns),
        "rows": [dict(row) for row in answer.rows],
        "row_count": answer.row_count,
        "duration_ms": answer.duration_ms,
    }


@router.get("/queries", response_model=AnalyticsQueryListResponse)
def list_queries(
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    dataset: str | None = Query(default=None, max_length=32),
    status: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
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
    row = SqlQueryRepository(session).get(identity, query_id)
    if row is None:
        # Another user's query must look exactly like a missing one.
        raise NotFoundError("Query not found.")
    return _summary(row)


def _summary(row: Any) -> dict[str, Any]:
    return {
        "query_id": row.id,
        "question": row.question,
        "dataset": row.dataset,
        "metric": row.metric,
        "group_by": row.group_by,
        "row_status": row.row_status,
        "time_range": row.time_range,
        "sql_text": row.sql_text,
        "row_count": row.row_count,
        "duration_ms": row.duration_ms,
        "status": row.status,
        "rejected_reason": row.rejected_reason,
        "trace_id": row.trace_id,
        "created_at": row.created_at.isoformat(),
    }
