"""Reading a customer's own rows and adding them up before any model speaks.

The scope is never negotiated: every statement filters by the ``Identity``
the server already authenticated. The question decides which record set and
which time window, never whose rows.

Small results are fetched and summed in Python, so the snapshot can also
answer a follow-up. Over the threshold, the aggregate stays in SQL and only
the most recent ``max_rows`` rows are read for L2; the snapshot says so
instead of pretending the detail is complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.security import Identity
from app.core.time import utcnow
from app.models.commerce import Order, Refund
from app.models.ticket import Ticket
from app.sql.planner import SqlPlan
from app.sql.schema import DATASETS, Dataset, status_label
from app.sql.snapshot import AnalyticsSnapshot, FactGroup, RecentFact

__all__ = ["PrefetchService"]


@dataclass(frozen=True, slots=True)
class _Spec:
    model: type[Any]
    amount_column: Any
    status_column: Any
    time_column: Any
    reference_column: Any


_SPECS: dict[str, _Spec] = {
    "orders": _Spec(
        model=Order,
        amount_column=Order.total_cents,
        status_column=Order.status,
        time_column=Order.created_at,
        reference_column=Order.id,
    ),
    "refunds": _Spec(
        model=Refund,
        amount_column=Refund.amount_cents,
        status_column=Refund.status,
        time_column=Refund.created_at,
        reference_column=Refund.order_id,
    ),
    "tickets": _Spec(
        model=Ticket,
        amount_column=None,
        status_column=Ticket.status,
        time_column=Ticket.created_at,
        reference_column=Ticket.ticket_no,
    ),
}


class PrefetchService:
    """Builds one snapshot from the caller's rows."""

    def __init__(
        self,
        *,
        max_rows: int = 200,
        recent_limit: int = 8,
        max_context_chars: int = 1400,
        ttl_seconds: int = 900,
    ) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be positive")
        if recent_limit < 0:
            raise ValueError("recent_limit must not be negative")
        if max_context_chars < 1:
            raise ValueError("max_context_chars must be positive")
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        self.max_rows = max_rows
        self.recent_limit = recent_limit
        self.max_context_chars = max_context_chars
        self.ttl_seconds = ttl_seconds

    def count(
        self, plan: SqlPlan, identity: Identity, session: Session
    ) -> int:
        """The scoped row count the router reads; never a model estimate."""
        dataset = DATASETS.get(plan.dataset)
        spec = _SPECS.get(plan.dataset)
        if dataset is None or spec is None:
            return 0
        conditions = _conditions(plan, identity, spec)
        value = session.scalar(
            select(func.count()).select_from(spec.model).where(*conditions)
        )
        return int(value or 0)

    def build(
        self,
        plan: SqlPlan,
        identity: Identity,
        session: Session,
        *,
        query_id: str,
        now: datetime | None = None,
    ) -> AnalyticsSnapshot:
        dataset = DATASETS.get(plan.dataset)
        spec = _SPECS.get(plan.dataset)
        if dataset is None or spec is None:
            raise ValueError(f"{plan.dataset} is not a prefetchable dataset")
        moment = now or utcnow()
        metric = _effective_metric(plan.metric, dataset)
        conditions = _conditions(plan, identity, spec)
        count, total_cents = _aggregate(session, spec, dataset, conditions, metric)
        truncated = count > self.max_rows

        fetch_limit = self.max_rows if truncated else count
        statement: Select[Any] = (
            select(spec.model)
            .where(*conditions)
            .order_by(spec.time_column.desc(), spec.model.id.desc())
        )
        rows = tuple(session.scalars(statement.limit(fetch_limit)).all())

        if truncated:
            groups = _sql_groups(
                session, spec, dataset, conditions, plan.group_by
            )
        else:
            count = len(rows)
            total_cents = _python_total(rows, spec)
            groups = _python_groups(rows, spec, dataset, plan.group_by)

        recent = tuple(
            _recent(row, spec, dataset) for row in rows[: self.recent_limit]
        )
        views = tuple(_row_view(row, spec, dataset) for row in rows)
        return AnalyticsSnapshot(
            query_id=query_id,
            dataset=plan.dataset,
            label=dataset.label,
            unit=dataset.unit,
            metric=metric,
            group_by=plan.group_by,
            time_range=plan.time_range.label if plan.time_range else "全部时间",
            count=count,
            total_cents=total_cents,
            groups=groups,
            recent=recent,
            rows=views,
            total_rows=count,
            truncated=truncated,
            created_at=moment,
            expires_at=moment + timedelta(seconds=self.ttl_seconds),
            context_budget=self.max_context_chars,
        )


def _effective_metric(metric: str, dataset: Dataset) -> str:
    if dataset.amount_column is None and metric in {"sum", "count_and_sum"}:
        return "count"
    return metric


def _conditions(
    plan: SqlPlan, identity: Identity, spec: _Spec
) -> list[Any]:
    conditions: list[Any] = [
        spec.model.tenant_id == identity.tenant_id,
        spec.model.user_id == identity.user_id,
    ]
    if plan.time_range is not None:
        conditions.append(spec.time_column >= plan.time_range.start)
        conditions.append(spec.time_column < plan.time_range.end)
    if plan.status is not None:
        conditions.append(spec.status_column == plan.status)
    return conditions


def _aggregate(
    session: Session,
    spec: _Spec,
    dataset: Dataset,
    conditions: list[Any],
    metric: str,
) -> tuple[int, int]:
    count = session.scalar(
        select(func.count()).select_from(spec.model).where(*conditions)
    )
    total = 0
    if dataset.amount_column is not None:
        total = session.scalar(
            select(func.coalesce(func.sum(spec.amount_column), 0)).where(*conditions)
        )
    return int(count or 0), int(total or 0)


def _python_total(rows: tuple[Any, ...], spec: _Spec) -> int:
    return sum(_row_cents(row, spec) for row in rows)


def _python_groups(
    rows: tuple[Any, ...],
    spec: _Spec,
    dataset: Dataset,
    group_by: str | None,
) -> tuple[FactGroup, ...]:
    if group_by is None:
        return ()
    buckets: dict[str, list[int]] = {}
    for row in rows:
        bucket = _bucket(row, group_by)
        entry = buckets.setdefault(bucket, [0, 0])
        entry[0] += 1
        entry[1] += _row_cents(row, spec)
    return tuple(
        FactGroup(
            bucket=bucket,
            label=status_label(dataset.name, bucket)
            if group_by == "status"
            else bucket,
            count=values[0],
            total_cents=values[1],
        )
        for bucket, values in sorted(buckets.items())
    )


def _sql_groups(
    session: Session,
    spec: _Spec,
    dataset: Dataset,
    conditions: list[Any],
    group_by: str | None,
) -> tuple[FactGroup, ...]:
    """The aggregate for an over-threshold detail question stays in SQL."""
    if group_by is None:
        return ()
    bucket = _group_expression(session, spec, group_by)
    total = (
        func.coalesce(func.sum(spec.amount_column), 0)
        if dataset.amount_column is not None
        else func.count()
    )
    statement = (
        select(
            bucket.label("bucket"),
            func.count().label("metric"),
            total.label("total"),
        )
        .where(*conditions)
        .group_by(bucket)
        .order_by(bucket)
    )
    groups: list[FactGroup] = []
    for row in session.execute(statement).all():
        raw = row[0]
        value = str(getattr(raw, "value", raw))
        groups.append(
            FactGroup(
                bucket=value,
                label=status_label(dataset.name, value)
                if group_by == "status"
                else value,
                count=int(row[1] or 0),
                total_cents=int(row[2] or 0),
            )
        )
    return tuple(groups)


def _group_expression(session: Session, spec: _Spec, group_by: str) -> Any:
    if group_by == "status":
        return spec.status_column
    fmt = "%Y-%m" if group_by == "month" else "%Y-%m-%d"
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return func.to_char(spec.time_column, "YYYY-MM" if group_by == "month" else "YYYY-MM-DD")
    return func.strftime(fmt, spec.time_column)


def _bucket(row: Any, group_by: str) -> str:
    if group_by == "status":
        value = row.status
        return str(getattr(value, "value", value))
    moment: datetime = row.created_at
    if group_by == "month":
        return moment.strftime("%Y-%m")
    return moment.strftime("%Y-%m-%d")


def _row_cents(row: Any, spec: _Spec) -> int:
    for attribute in ("total_cents", "amount_cents"):
        value = getattr(row, attribute, None)
        if isinstance(value, int):
            return value
    return 0


def _recent(row: Any, spec: _Spec, dataset: Dataset) -> RecentFact:
    moment: datetime = getattr(row, spec.time_column.key)
    status = getattr(row, spec.status_column.key)
    status_value = str(getattr(status, "value", status))
    amount = _row_cents(row, spec) if dataset.amount_column is not None else None
    return RecentFact(
        at=moment.strftime("%Y-%m-%d"),
        status=status_label(dataset.name, status_value),
        amount_cents=amount,
        reference=str(getattr(row, spec.reference_column.key)),
    )


def _row_view(row: Any, spec: _Spec, dataset: Dataset) -> dict[str, Any]:
    moment: datetime = getattr(row, spec.time_column.key)
    status = getattr(row, spec.status_column.key)
    status_value = str(getattr(status, "value", status))
    view: dict[str, Any] = {
        "id": str(row.id),
        "reference": str(getattr(row, spec.reference_column.key)),
        "created_at": moment.isoformat(timespec="seconds"),
        "status": status_value,
        "status_label": status_label(dataset.name, status_value),
    }
    if dataset.amount_column is not None:
        view["amount_cents"] = _row_cents(row, spec)
    return view
