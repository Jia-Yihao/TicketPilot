"""Where a statistics question is answered: SQL, prefetch, or a refusal.

The router is deterministic on purpose. It reads the question's shape, the
row count the caller already measured with a scoped ``COUNT``, and the
configuration; it never asks a model and never guesses a row count. Two runs
with the same inputs must return the same route and the same reason, because
the reason is what the trace and the tests count.

The rules mirror the P3-b contract:

* an unsupported dimension keeps P3-a's refusal — a human checks it;
* detail / explanation questions need L2/L3 rows, so they prefetch;
* an enumerable dimension (status / month / day) keeps aggregation in SQL;
* a pure statistic over the threshold keeps aggregation in SQL;
* everything else prefetches, where Python can add up the same rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.sql.planner import PlanRefusal, SqlPlan

__all__ = [
    "AnalyticsRoute",
    "AnalyticsRouter",
    "RouteDecision",
    "asks_for_detail",
    "asks_for_detail_followup",
]


class AnalyticsRoute(StrEnum):
    """The three outcomes a question can route to."""

    SQL = "SQL"
    PREFETCH = "PREFETCH"
    REFUSED = "REFUSED"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """The route and the countable reason behind it."""

    route: AnalyticsRoute
    reason: str


# Detail cues. "最近" is deliberately not a bare cue: "最近 30 天" is a time
# range, while "最近怎么样" asks to see what happened. The negative lookahead
# separates the two without a model.
_DETAIL_CUES: tuple[str, ...] = (
    "哪几",
    "哪一笔",
    "哪两笔",
    "哪三笔",
    "哪笔",
    "明细",
    "具体",
    "列出来",
    "列出",
    "分别是什么",
    "展开",
    "帮我看看",
)
_RECENT_DETAIL = re.compile(r"最近(?!\s*(?:\d+|今|昨|本|上))")


def asks_for_detail(text: str) -> bool:
    """True when the question asks to see rows, not only an aggregate."""
    lowered = text.lower()
    if any(cue in lowered for cue in _DETAIL_CUES):
        return True
    return _RECENT_DETAIL.search(lowered) is not None


# A follow-up is narrower than a detail question: it must not be a new
# statistic ("订单明细" names a record set) and it must point at rows rather
# than ask for an explanation ("具体怎么退款" is a policy question).
_FOLLOWUP_CUES: tuple[str, ...] = (
    "哪几",
    "哪一笔",
    "哪两笔",
    "哪三笔",
    "哪笔",
    "明细",
    "列出来",
    "列出",
    "分别是什么",
)


def asks_for_detail_followup(text: str) -> bool:
    """True when the message opens the previous statistic, not a new one."""
    return any(cue in text.lower() for cue in _FOLLOWUP_CUES)


class AnalyticsRouter:
    """Chooses the answer path from question shape, row count and settings."""

    def __init__(
        self, *, max_rows: int = 200, prefetch_enabled: bool = True
    ) -> None:
        self.max_rows = max_rows
        self.prefetch_enabled = prefetch_enabled

    def route(
        self,
        question: str,
        plan: SqlPlan | None,
        *,
        row_count: int,
        refusal: PlanRefusal | None = None,
    ) -> RouteDecision:
        """The route for one question; ``row_count`` is already scoped."""
        if plan is None:
            reason = refusal.reason if refusal is not None else "NO_PLAN"
            return RouteDecision(AnalyticsRoute.REFUSED, reason)
        if not self.prefetch_enabled:
            return RouteDecision(AnalyticsRoute.SQL, "PREFETCH_DISABLED")
        if asks_for_detail(question):
            return RouteDecision(AnalyticsRoute.PREFETCH, "DETAIL_REQUEST")
        if plan.group_by in {"status", "month", "day"}:
            return RouteDecision(AnalyticsRoute.SQL, "ENUMERABLE_DIMENSION")
        if row_count > self.max_rows:
            return RouteDecision(AnalyticsRoute.SQL, "ROW_COUNT_OVER_THRESHOLD")
        return RouteDecision(AnalyticsRoute.PREFETCH, "SMALL_DATASET")
