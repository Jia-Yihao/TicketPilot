from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.observability import TraceSpan
from app.observability.tracing import TraceRecorder


class TraceRepository:
    """Reads and writes trace spans."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        trace_id: str,
        tenant_id: str,
        kind: str,
        name: str,
        attributes: dict[str, Any] | None = None,
        parent_span_id: str | None = None,
        conversation_id: str | None = None,
        status: str = "OK",
        latency_ms: int = 0,
        span_id: str | None = None,
    ) -> str:
        span_id = span_id or f"spn_{uuid.uuid4().hex[:16]}"
        self.session.add(
            TraceSpan(
                id=span_id,
                trace_id=trace_id,
                parent_span_id=parent_span_id,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                kind=kind,
                name=name,
                status=status,
                attributes=attributes or {},
                latency_ms=latency_ms,
            )
        )
        self.session.flush()
        return span_id

    def flush(self, recorder: TraceRecorder) -> int:
        """Write every buffered span of a recorder; returns the span count.

        Finalising first makes this the one call site where a tree grows its
        root span (R9 口径 §2.3.2) — callers never add one themselves.
        """
        recorder.finalize()
        for span in recorder.spans:
            self.record(
                trace_id=recorder.trace_id,
                tenant_id=recorder.tenant_id,
                kind=span.kind,
                name=span.name,
                attributes=span.attributes,
                parent_span_id=span.parent_span_id,
                conversation_id=recorder.conversation_id,
                status=span.status,
                latency_ms=span.latency_ms,
                span_id=span.id,
            )
        return len(recorder.spans)

    def list_for_trace(self, trace_id: str) -> list[TraceSpan]:
        return list(
            self.session.scalars(
                select(TraceSpan)
                .where(TraceSpan.trace_id == trace_id)
                .order_by(TraceSpan.started_at, TraceSpan.id)
            )
        )

    def list_for_conversation(
        self, conversation_id: str, *, limit: int = 200
    ) -> list[TraceSpan]:
        return list(
            self.session.scalars(
                select(TraceSpan)
                .where(TraceSpan.conversation_id == conversation_id)
                .order_by(TraceSpan.started_at, TraceSpan.id)
                .limit(limit)
            )
        )
