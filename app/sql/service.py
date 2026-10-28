"""Running a read-only question against the ledger.

The service owns the whole path, and each step refuses to continue on its
own:

1. the planner writes down what was asked (or refuses: ``NO_PLAN``);
2. the builder turns the plan into a statement;
3. the guard checks the statement and injects the tenant and owner scope;
4. the statement runs on a connection that cannot write;
5. the run is written to the ledger and to the trace.

The connection matters as much as the guard: on PostgreSQL the transaction
is marked read-only, on SQLite the connection is put into query-only mode
and thrown away afterwards, so a mistake in the guard still cannot write.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.core.security import Identity
from app.core.time import utcnow
from app.models.sql_query import SqlQuery
from app.observability.redaction import digest, redact_payload
from app.observability.tracing import TraceRecorder
from app.sql.builder import build_statement
from app.sql.errors import SqlRejected
from app.sql.guard import SqlGuard
from app.sql.parser import render
from app.sql.planner import (
    HeuristicSqlPlanner,
    PlanRefusal,
    SqlPlan,
    SqlPlanner,
)
from app.sql.prefetch import PrefetchService
from app.sql.router import AnalyticsRoute, AnalyticsRouter, RouteDecision
from app.sql.schema import dataset_for
from app.sql.snapshot import AnalyticsSnapshot, SnapshotStore

__all__ = ["SqlAnalyticsService", "SqlAnswer"]


@dataclass(frozen=True, slots=True)
class SqlAnswer:
    """The result of one question, and the statement that produced it.

    ``route`` says which path answered: ``SQL`` keeps the P3-a shape,
    ``PREFETCH`` carries a snapshot whose L3 rows live in the store.
    """

    plan: SqlPlan
    sql: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    row_count: int
    duration_ms: int
    query_id: str
    route: str = "SQL"
    snapshot: AnalyticsSnapshot | None = None


class SqlAnalyticsService:
    """Answers a customer's question about their own records."""

    def __init__(
        self,
        *,
        guard: SqlGuard | None = None,
        planner: SqlPlanner | None = None,
        max_limit: int = 200,
        prefetch_enabled: bool = True,
        prefetch_max_rows: int = 200,
        snapshot_ttl_seconds: int = 900,
        snapshot_store: SnapshotStore | None = None,
    ) -> None:
        self.guard = guard or SqlGuard(max_limit=max_limit)
        self.planner = planner or HeuristicSqlPlanner()
        self.max_limit = max_limit
        self.prefetch_enabled = prefetch_enabled
        self.prefetch_max_rows = prefetch_max_rows
        self.prefetch = PrefetchService(
            max_rows=prefetch_max_rows,
            ttl_seconds=snapshot_ttl_seconds,
        )
        self.router = AnalyticsRouter(
            max_rows=prefetch_max_rows, prefetch_enabled=prefetch_enabled
        )
        self.snapshots = snapshot_store or SnapshotStore()

    # ------------------------------------------------------------ answering
    def answer(
        self,
        question: str,
        identity: Identity,
        session: Session,
        *,
        trace: TraceRecorder | None = None,
    ) -> SqlAnswer:
        """Answer the question, or refuse with a reason code."""
        plan, refusal = self._decide(question)
        if plan is None:
            reason = refusal.reason if refusal is not None else "NO_PLAN"
            detail = (
                refusal.detail
                if refusal is not None and refusal.detail
                else "the question does not name a record set"
            )
            decision = self.router.route(
                question, None, row_count=0, refusal=refusal
            )
            self._trace_route(trace, decision, None, row_count=0, query_id=None)
            self._record(
                session,
                identity,
                None,
                question,
                "",
                {},
                row_count=0,
                duration_ms=0,
                status="REJECTED",
                rejected_reason=reason,
                trace_id=trace.trace_id if trace else None,
            )
            raise SqlRejected(reason, detail)
        dataset = dataset_for(plan.dataset)
        if dataset is None:
            decision = RouteDecision(AnalyticsRoute.REFUSED, "UNKNOWN_TABLE")
            self._trace_route(trace, decision, plan, row_count=0, query_id=None)
            self._record(
                session,
                identity,
                plan,
                plan.question,
                "",
                {},
                row_count=0,
                duration_ms=0,
                status="REJECTED",
                rejected_reason="UNKNOWN_TABLE",
                trace_id=trace.trace_id if trace else None,
            )
            raise SqlRejected("UNKNOWN_TABLE", f"{plan.dataset} is not readable")

        row_count = self.prefetch.count(plan, identity, session)
        decision = self.router.route(question, plan, row_count=row_count)
        if decision.route is AnalyticsRoute.PREFETCH:
            return self._answer_prefetch(
                plan,
                identity,
                session,
                question=question,
                row_count=row_count,
                decision=decision,
                trace=trace,
            )
        self._trace_route(trace, decision, plan, row_count=row_count, query_id=None)

        engine = session.get_bind()
        if not isinstance(engine, Engine):
            raise SqlRejected("NO_PLAN", "no database engine is bound")
        dialect = engine.dialect.name

        try:
            statement, params = build_statement(
                plan, dataset, dialect=dialect, max_limit=self.max_limit
            )
            self.guard.check(
                statement,
                allowed_params=set(params) | {"tenant_id", "user_id"},
            )
            scoped = self.guard.scope(statement)
            self.guard.require_scope(scoped)
        except SqlRejected as exc:
            self._record(
                session,
                identity,
                plan,
                plan.question,
                "",
                {},
                row_count=0,
                duration_ms=0,
                status="REJECTED",
                rejected_reason=exc.reason,
                trace_id=trace.trace_id if trace else None,
            )
            raise
        params["tenant_id"] = identity.tenant_id
        params["user_id"] = identity.user_id
        sql, bound = render(scoped, params)

        started = time.perf_counter()
        try:
            columns, rows = self._execute(engine, sql, bound, dialect)
        except SqlRejected:
            raise
        except Exception as exc:  # noqa: BLE001 - the reason is recorded, not raised
            self._record(
                session,
                identity,
                plan,
                plan.question,
                sql,
                bound,
                row_count=0,
                duration_ms=int((time.perf_counter() - started) * 1000),
                status="FAILED",
                rejected_reason="EXECUTION_FAILED",
                trace_id=trace.trace_id if trace else None,
            )
            raise SqlRejected("EXECUTION_FAILED", type(exc).__name__) from exc
        duration_ms = int((time.perf_counter() - started) * 1000)

        row = self._record(
            session,
            identity,
            plan,
            plan.question,
            sql,
            bound,
            row_count=len(rows),
            duration_ms=duration_ms,
            status="SUCCEEDED",
            rejected_reason=None,
            trace_id=trace.trace_id if trace else None,
        )
        if trace is not None:
            trace.add(
                "tool",
                "analytics.query",
                attributes={
                    "dataset": plan.dataset,
                    "metric": plan.metric,
                    "group_by": plan.group_by,
                    "range": plan.time_range.label if plan.time_range else None,
                    "row_count": len(rows),
                    "duration_ms": duration_ms,
                    "sql": sql,
                    "query_id": row.id,
                },
            )
        return SqlAnswer(
            plan=plan,
            sql=sql,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            duration_ms=duration_ms,
            query_id=row.id,
            route=AnalyticsRoute.SQL.value,
        )

    # ------------------------------------------------------------ snapshots
    def get_snapshot(
        self, query_id: str, identity: Identity
    ) -> AnalyticsSnapshot | None:
        """The L3 rows behind one answer, if they are still this caller's."""
        return self.snapshots.get(query_id, identity)

    # ------------------------------------------------------------ prefetch
    def _answer_prefetch(
        self,
        plan: SqlPlan,
        identity: Identity,
        session: Session,
        *,
        question: str,
        row_count: int,
        decision: RouteDecision,
        trace: TraceRecorder | None,
    ) -> SqlAnswer:
        """Rows are read and summed before any model is asked to speak."""
        query_id = f"sql_{uuid.uuid4().hex[:16]}"
        started = time.perf_counter()
        snapshot = self.prefetch.build(
            plan, identity, session, query_id=query_id
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        self._record(
            session,
            identity,
            plan,
            plan.question,
            "",
            {},
            row_count=len(snapshot.l1_rows()),
            duration_ms=duration_ms,
            status="SUCCEEDED",
            rejected_reason=None,
            trace_id=trace.trace_id if trace else None,
            row_id=query_id,
        )
        self.snapshots.put(snapshot, identity)
        self._trace_route(
            trace, decision, plan, row_count=row_count, query_id=query_id
        )
        if trace is not None:
            context = snapshot.prompt_context()
            trace.add(
                "tool",
                "analytics.query",
                attributes={
                    "dataset": plan.dataset,
                    "metric": plan.metric,
                    "group_by": plan.group_by,
                    "range": plan.time_range.label if plan.time_range else None,
                    "row_count": len(snapshot.rows),
                    "duration_ms": duration_ms,
                    "sql": "",
                    "query_id": query_id,
                    "route": AnalyticsRoute.PREFETCH.value,
                    "rows_returned": len(snapshot.rows),
                    "truncated": snapshot.truncated,
                },
            )
            trace.add(
                "tool",
                "analytics.prefetch",
                attributes={
                    "query_id": query_id,
                    "rows_returned": len(snapshot.rows),
                    "truncated": snapshot.truncated,
                    "context_chars": context["chars"],
                    "estimated_tokens": context["estimated_tokens"],
                    "duration_ms": duration_ms,
                },
            )
        rows = snapshot.l1_rows()
        return SqlAnswer(
            plan=plan,
            sql="",
            columns=_prefetch_columns(plan, snapshot.metric),
            rows=rows,
            row_count=len(rows),
            duration_ms=duration_ms,
            query_id=query_id,
            route=AnalyticsRoute.PREFETCH.value,
            snapshot=snapshot,
        )

    def _trace_route(
        self,
        trace: TraceRecorder | None,
        decision: RouteDecision,
        plan: SqlPlan | None,
        *,
        row_count: int,
        query_id: str | None,
    ) -> None:
        if trace is None:
            return
        trace.add(
            "tool",
            "analytics.route",
            attributes={
                "route": decision.route.value,
                "route_reason": decision.reason,
                "dataset": plan.dataset if plan is not None else None,
                "row_count": row_count,
                "threshold": self.prefetch_max_rows,
                "query_id": query_id,
            },
        )

    # ------------------------------------------------------------ execution
    def _decide(self, question: str) -> tuple[SqlPlan | None, PlanRefusal | None]:
        """Ask the planner for a plan and, if there is none, why.

        The protocol only promises ``plan()``. A planner that also exposes
        ``decide()`` gives the ledger a countable reason; older planners keep
        working and are reported as ``NO_PLAN``.
        """
        decide = getattr(self.planner, "decide", None)
        if callable(decide):
            return cast(
                tuple[SqlPlan | None, PlanRefusal | None],
                decide(question, now=utcnow()),
            )
        return self.planner.plan(question, now=utcnow()), None

    def _execute(
        self,
        engine: Engine,
        sql: str,
        params: dict[str, Any],
        dialect: str,
    ) -> tuple[tuple[str, ...], tuple[dict[str, Any], ...]]:
        connection = engine.connect()
        try:
            if dialect == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            elif dialect == "sqlite":
                connection.exec_driver_sql("PRAGMA query_only=ON")
            result = connection.execute(text(sql), self._adapt(params, dialect))
            columns = tuple(result.keys())
            rows = tuple(
                redact_payload(dict(row)) for row in result.mappings().all()
            )
        finally:
            self._release(connection, dialect)
        return columns, rows

    def _release(self, connection: Connection, dialect: str) -> None:
        """End the read and hand the connection back without leaking state."""
        try:
            connection.rollback()
        except Exception:  # noqa: BLE001 - the connection is discarded anyway
            pass
        if dialect == "sqlite":
            try:
                connection.exec_driver_sql("PRAGMA query_only=OFF")
            except Exception:  # noqa: BLE001 - a poisoned connection is discarded
                connection.invalidate()
        connection.close()

    def _adapt(self, params: dict[str, Any], dialect: str) -> dict[str, Any]:
        """SQLite stores timestamps as text; bind them the same way."""
        if dialect != "sqlite":
            return params
        adapted: dict[str, Any] = {}
        for key, value in params.items():
            if isinstance(value, datetime):
                adapted[key] = value.strftime("%Y-%m-%d %H:%M:%S.%f")
            else:
                adapted[key] = value
        return adapted

    # --------------------------------------------------------------- ledger
    def _record(
        self,
        session: Session,
        identity: Identity,
        plan: SqlPlan | None,
        question: str,
        sql: str,
        params: dict[str, Any],
        *,
        row_count: int,
        duration_ms: int,
        status: str,
        rejected_reason: str | None,
        trace_id: str | None,
        row_id: str | None = None,
    ) -> SqlQuery:
        row = SqlQuery(
            id=row_id or f"sql_{uuid.uuid4().hex[:16]}",
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            question=str(redact_payload(question)),
            dataset=plan.dataset if plan is not None else "",
            metric=plan.metric if plan is not None else "",
            group_by=plan.group_by if plan is not None else None,
            row_status=plan.status if plan is not None else None,
            time_range=(
                plan.time_range.label
                if plan is not None and plan.time_range is not None
                else None
            ),
            sql_text=sql,
            params_digest=_param_digest(params),
            row_count=row_count,
            duration_ms=duration_ms,
            status=status,
            rejected_reason=rejected_reason,
            trace_id=trace_id,
        )
        session.add(row)
        return row


def _prefetch_columns(plan: SqlPlan, metric: str) -> tuple[str, ...]:
    """The columns of a prefetch answer mirror the SQL builder's shape."""
    columns: list[str] = []
    if plan.group_by is not None:
        columns.append("bucket")
    if metric in {"count", "count_and_sum"}:
        columns.append("metric")
    if metric in {"sum", "count_and_sum"}:
        columns.append("metric_total")
    return tuple(columns)


def _param_digest(params: dict[str, Any]) -> str:
    """A fingerprint of the bound values; the values themselves stay out."""
    parts: list[str] = []
    for key in sorted(params):
        value = params[key]
        if isinstance(value, datetime):
            parts.append(f"{key}={value.isoformat()}")
        elif isinstance(value, bool) or isinstance(value, (int, float, str)):
            parts.append(f"{key}={digest(str(value))}")
        else:
            parts.append(f"{key}=?")
    return digest("|".join(parts), length=64)
