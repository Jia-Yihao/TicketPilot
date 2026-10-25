"""Cross-worker model accounting.

The runtime and the chat gap path each write one row per model run; this
service owns the write shape and the tenant-scoped read. A caller that
already holds a transaction passes its session (the task runtime does, so
the ledger row commits with ``task.routed``). A caller on the request edge
with no transaction of its own opens a short one and fails open: accounting
must never turn into an availability dependency.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.llm_usage import LlmUsageRecord

__all__ = ["LlmUsageService", "MAX_WINDOW_HOURS"]

MAX_WINDOW_HOURS = 24 * 90
# The aggregate reads the window into memory; one tenant's model calls are
# small by design, and the cap keeps a pathological tenant bounded.
_MAX_SCAN_ROWS = 20_000


class LlmUsageService:
    """Write usage rows; callers own the transaction boundary."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        tenant_id: str,
        source: str,
        planning_source: str,
        requests: int = 0,
        retries: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: float = 0.0,
        status: str = "OK",
        model_name: str | None = None,
        user_id: str | None = None,
        conversation_id: str | None = None,
        task_id: str | None = None,
    ) -> LlmUsageRecord:
        row = LlmUsageRecord(
            id=f"llm_{uuid.uuid4().hex[:16]}",
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            task_id=task_id,
            source=source,
            planning_source=planning_source,
            model_name=model_name,
            requests=max(0, requests),
            retries=max(0, retries),
            input_tokens=max(0, input_tokens),
            output_tokens=max(0, output_tokens),
            latency_ms=max(0, int(round(latency_ms))),
            status=status,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def summary(
        self,
        tenant_id: str,
        *,
        hours: int = 24,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Totals, per-source split and the latest rows inside the window.

        The aggregates are computed in Python because the row count of a
        single tenant's model calls is small by design; that keeps the same
        code path on SQLite and PostgreSQL.
        """
        cutoff = utcnow() - timedelta(hours=hours)
        rows = list(
            self.session.scalars(
                select(LlmUsageRecord)
                .where(
                    LlmUsageRecord.tenant_id == tenant_id,
                    LlmUsageRecord.created_at >= cutoff,
                )
                .order_by(LlmUsageRecord.created_at.desc())
                .limit(_MAX_SCAN_ROWS)
            )
        )
        totals = _aggregate(rows)
        sources: dict[str, list[LlmUsageRecord]] = {}
        for row in rows:
            sources.setdefault(row.source, []).append(row)
        return {
            "window_hours": hours,
            "totals": totals,
            "by_source": [
                {"source": source, **_aggregate(items)}
                for source, items in sorted(sources.items())
            ],
            "recent": [_row_view(row) for row in rows[:limit]],
        }


def _aggregate(rows: list[LlmUsageRecord]) -> dict[str, Any]:
    calls = len(rows)
    latency = sum(row.latency_ms for row in rows)
    return {
        "calls": calls,
        "failed": sum(1 for row in rows if row.status != "OK"),
        "requests": sum(row.requests for row in rows),
        "retries": sum(row.retries for row in rows),
        "input_tokens": sum(row.input_tokens for row in rows),
        "output_tokens": sum(row.output_tokens for row in rows),
        "avg_latency_ms": round(latency / calls, 1) if calls else 0.0,
    }


def _row_view(row: LlmUsageRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "source": row.source,
        "planning_source": row.planning_source,
        "model_name": row.model_name,
        "conversation_id": row.conversation_id,
        "task_id": row.task_id,
        "requests": row.requests,
        "retries": row.retries,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "latency_ms": row.latency_ms,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
    }
