"""Controlled text-to-SQL.

The module answers one kind of question — "how many refunds did I get last
month, and how much money was that" — without letting a question become
arbitrary SQL. The path is always the same:

    question -> plan -> statement -> guard -> read-only connection -> rows

Every step can refuse, and a refusal carries a reason code. The guard is
the security boundary; the read-only connection is the second line of
defence behind it.
"""

from __future__ import annotations

from app.sql.builder import build_statement
from app.sql.errors import REASON_CODES, SqlRejected
from app.sql.guard import SqlGuard
from app.sql.parser import parse_select, render
from app.sql.planner import HeuristicSqlPlanner, SqlPlan, SqlPlanner, TimeRange
from app.sql.schema import DATASETS, Dataset, dataset_for
from app.sql.service import SqlAnalyticsService, SqlAnswer

__all__ = [
    "DATASETS",
    "Dataset",
    "HeuristicSqlPlanner",
    "REASON_CODES",
    "SqlAnalyticsService",
    "SqlAnswer",
    "SqlGuard",
    "SqlPlan",
    "SqlPlanner",
    "SqlRejected",
    "TimeRange",
    "build_statement",
    "dataset_for",
    "parse_select",
    "render",
]
