"""Turning a query plan into a statement the guard will accept.

The builder writes SQL as a tree, not as text, so the same parser the guard
uses can read the result back. Nothing here decides *what* to query — the
plan does that — and nothing here can widen the scope: the tenant and the
owner are added by the executor afterwards.
"""

from __future__ import annotations

from typing import Any

from app.sql.parser import (
    ColumnRef,
    Comparison,
    Expr,
    FuncCall,
    InlineLiteral,
    Literal,
    Logical,
    Param,
    SelectItem,
    SelectStatement,
)
from app.sql.planner import SqlPlan
from app.sql.schema import Dataset

__all__ = ["build_statement", "PERIOD_FORMATS"]


# The date format each dialect needs to bucket a timestamp by month or day.
PERIOD_FORMATS: dict[str, dict[str, str]] = {
    "sqlite": {"month": "%Y-%m", "day": "%Y-%m-%d"},
    "postgresql": {"month": "YYYY-MM", "day": "YYYY-MM-DD"},
}

_PERIOD_FUNCTIONS: dict[str, str] = {
    "sqlite": "STRFTIME",
    "postgresql": "TO_CHAR",
}


def build_statement(
    plan: SqlPlan,
    dataset: Dataset,
    *,
    dialect: str,
    max_limit: int = 200,
) -> tuple[SelectStatement, dict[str, Any]]:
    """Build the statement and the parameters it needs."""
    if dataset.amount_column is None and plan.metric in {"sum", "count_and_sum"}:
        plan = SqlPlan(
            dataset=plan.dataset,
            metric="count",
            group_by=plan.group_by,
            time_range=plan.time_range,
            question=plan.question,
            status=plan.status,
        )

    items: list[SelectItem] = []
    group_by: tuple[Expr, ...] = ()
    if plan.group_by == "status" and dataset.status_column is not None:
        bucket: Expr = ColumnRef(None, dataset.status_column)
        items.append(SelectItem(expr=bucket, alias="bucket"))
        group_by = (bucket,)
    elif plan.group_by in {"month", "day"}:
        function = _PERIOD_FUNCTIONS.get(dialect, "STRFTIME")
        fmt = PERIOD_FORMATS.get(dialect, PERIOD_FORMATS["sqlite"])[plan.group_by]
        bucket = _period_expression(function, fmt, dataset.time_column)
        items.append(SelectItem(expr=bucket, alias="bucket"))
        group_by = (bucket,)

    if plan.metric in {"count", "count_and_sum"}:
        items.append(SelectItem(expr=FuncCall("COUNT", (), star=True), alias="metric"))
    if plan.metric in {"sum", "count_and_sum"} and dataset.amount_column is not None:
        items.append(
            SelectItem(
                expr=FuncCall(
                    "COALESCE",
                    (
                        FuncCall("SUM", (ColumnRef(None, dataset.amount_column),)),
                        Literal("0"),
                    ),
                ),
                alias="metric_total",
            )
        )

    params: dict[str, Any] = {}
    predicates: list[Expr] = []
    if plan.time_range is not None:
        params["start"] = plan.time_range.start
        params["end"] = plan.time_range.end
        predicates.append(
            Comparison(ColumnRef(None, dataset.time_column), ">=", Param("start"))
        )
        predicates.append(
            Comparison(ColumnRef(None, dataset.time_column), "<", Param("end"))
        )
    if plan.status is not None and dataset.status_column is not None:
        params["status"] = plan.status
        predicates.append(
            Comparison(ColumnRef(None, dataset.status_column), "=", Param("status"))
        )
    where: Expr | None = None
    if predicates:
        where = Logical("and", tuple(predicates))

    statement = SelectStatement(
        items=tuple(items),
        table=dataset.table,
        alias=None,
        where=where,
        group_by=group_by,
        order_by=((ColumnRef(None, "bucket"), "ASC"),) if group_by else (),
        limit=max_limit,
    )
    return statement, params


def _period_expression(function: str, fmt: str, column: str) -> Expr:
    """SQLite takes the format first, PostgreSQL takes the column first."""
    if function == "STRFTIME":
        return FuncCall(function, (InlineLiteral(fmt), ColumnRef(None, column)))
    return FuncCall(function, (ColumnRef(None, column), InlineLiteral(fmt)))
