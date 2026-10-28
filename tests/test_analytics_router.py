"""P3-b routing: four deterministic branches and their reasons.

The router only reads the question, the plan, the row count and the
configuration. These cases pin each branch, the reason code, and the promise
that the same input always gets the same route.
"""

from __future__ import annotations

from datetime import datetime

from app.core.config import Settings
from app.sql.planner import HeuristicSqlPlanner, PlanRefusal
from app.sql.router import AnalyticsRoute, AnalyticsRouter

NOW = datetime(2026, 10, 25, 12, 0, 0)
PLANNER = HeuristicSqlPlanner()


def _plan(question: str):  # type: ignore[no-untyped-def]
    plan = PLANNER.plan(question, now=NOW)
    assert plan is not None
    return plan


def test_an_unsupported_dimension_is_refused_with_its_own_reason() -> None:
    decision = AnalyticsRouter().route(
        "按商品统计我的退款笔数",
        None,
        row_count=0,
        refusal=PlanRefusal("UNSUPPORTED_DIMENSION", "grouping by 商品 is not supported"),
    )
    assert decision.route is AnalyticsRoute.REFUSED
    assert decision.reason == "UNSUPPORTED_DIMENSION"


def test_a_detail_question_takes_the_prefetch_path() -> None:
    decision = AnalyticsRouter().route(
        "我买了多少订单，分别是哪几笔", _plan("我买了多少订单"), row_count=2
    )
    assert decision.route is AnalyticsRoute.PREFETCH
    assert decision.reason == "DETAIL_REQUEST"


def test_showing_the_records_takes_the_prefetch_path() -> None:
    decision = AnalyticsRouter().route(
        "帮我看看我的退款明细", _plan("我退了几笔款"), row_count=2
    )
    assert decision.route is AnalyticsRoute.PREFETCH
    assert decision.reason == "DETAIL_REQUEST"


def test_recent_as_a_time_window_is_not_a_detail_cue() -> None:
    """'最近 30 天' is a range; '最近怎么样' is a detail request."""
    stats = AnalyticsRouter().route(
        "最近30天我退了几笔款", _plan("最近30天我退了几笔款"), row_count=2
    )
    detail = AnalyticsRouter().route(
        "最近怎么样，帮我看看", _plan("最近30天我退了几笔款"), row_count=2
    )
    assert stats.route is AnalyticsRoute.PREFETCH
    assert stats.reason == "SMALL_DATASET"
    assert detail.reason == "DETAIL_REQUEST"


def test_an_enumerable_dimension_stays_on_the_sql_path() -> None:
    question = "按状态统计我的订单笔数"
    decision = AnalyticsRouter().route(question, _plan(question), row_count=2)
    assert decision.route is AnalyticsRoute.SQL
    assert decision.reason == "ENUMERABLE_DIMENSION"


def test_over_the_threshold_stays_on_the_sql_path() -> None:
    question = "我买了多少订单"
    decision = AnalyticsRouter(max_rows=1).route(
        question, _plan(question), row_count=2
    )
    assert decision.route is AnalyticsRoute.SQL
    assert decision.reason == "ROW_COUNT_OVER_THRESHOLD"


def test_a_small_statistic_takes_the_prefetch_path() -> None:
    question = "我买了多少订单"
    decision = AnalyticsRouter(max_rows=2).route(
        question, _plan(question), row_count=2
    )
    assert decision.route is AnalyticsRoute.PREFETCH
    assert decision.reason == "SMALL_DATASET"


def test_the_same_input_always_gets_the_same_route() -> None:
    router = AnalyticsRouter(max_rows=2)
    question = "我买了多少订单"
    first = router.route(question, _plan(question), row_count=2)
    second = router.route(question, _plan(question), row_count=2)
    assert first == second


def test_turning_prefetch_off_restores_the_sql_path() -> None:
    question = "我买了多少订单"
    decision = AnalyticsRouter(prefetch_enabled=False).route(
        question, _plan(question), row_count=2
    )
    assert decision.route is AnalyticsRoute.SQL
    assert decision.reason == "PREFETCH_DISABLED"


def test_a_missing_plan_is_refused_with_the_planners_reason() -> None:
    decision = AnalyticsRouter().route(
        "今天天气怎么样",
        None,
        row_count=0,
        refusal=PlanRefusal("NO_PLAN", "the question does not name a record set"),
    )
    assert decision.route is AnalyticsRoute.REFUSED
    assert decision.reason == "NO_PLAN"


def test_the_new_settings_have_defaults_and_can_be_overridden() -> None:
    defaults = Settings()
    assert defaults.analytics_prefetch_enabled is True
    assert defaults.analytics_prefetch_max_rows == 200
    assert defaults.analytics_snapshot_ttl_seconds == 900
    assert defaults.analytics_narration_enabled is True

    overridden = Settings(
        analytics_prefetch_enabled=False,
        analytics_prefetch_max_rows=7,
        analytics_snapshot_ttl_seconds=60,
        analytics_narration_enabled=False,
    )
    assert overridden.analytics_prefetch_enabled is False
    assert overridden.analytics_prefetch_max_rows == 7
    assert overridden.analytics_snapshot_ttl_seconds == 60
    assert overridden.analytics_narration_enabled is False
