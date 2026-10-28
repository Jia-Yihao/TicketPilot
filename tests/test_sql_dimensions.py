"""P3: a grouping the schema cannot answer is refused, not guessed.

The planner used to treat "分布" as "by status" regardless of what was being
grouped, so "我的退款按商品分布" produced true numbers for the wrong question.
These tests pin the new behaviour at three levels: the plan, the ledger reason,
and the customer-visible turn (refusal plus a real handoff).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import Identity
from app.models.sql_query import SqlQuery
from app.sql.errors import SqlRejected
from app.sql.planner import HeuristicSqlPlanner
from app.sql.service import SqlAnalyticsService
from tests.conftest import ALICE

NOW = datetime(2026, 10, 25, 12, 0, 0)
UNSUPPORTED = (
    "我的退款按商品分布",
    "按品类统计我的退款",
    "按渠道看看我的退款",
)
SUPPORTED = (
    ("按状态统计我的订单笔数", "status"),
    ("我的退款按月统计", "month"),
    ("按天看看我的退款", "day"),
)


@pytest.fixture
def planner() -> HeuristicSqlPlanner:
    return HeuristicSqlPlanner()


def test_unsupported_dimensions_produce_no_plan(planner: HeuristicSqlPlanner) -> None:
    for question in UNSUPPORTED:
        plan, refusal = planner.decide(question, now=NOW)
        assert plan is None, question
        assert refusal is not None and refusal.reason == "UNSUPPORTED_DIMENSION"


def test_supported_dimensions_still_plan(planner: HeuristicSqlPlanner) -> None:
    for question, dimension in SUPPORTED:
        plan = planner.plan(question, now=NOW)
        assert plan is not None, question
        assert plan.group_by == dimension


def test_the_ledger_distinguishes_no_plan_from_unsupported(
    app: FastAPI,
) -> None:
    service = SqlAnalyticsService(max_limit=200)
    alice = Identity(tenant_id="demo", user_id="alice", roles=frozenset())
    with app.state.session_factory() as session:
        with pytest.raises(SqlRejected) as no_plan:
            service.answer("今天天气怎么样", alice, session)
        with pytest.raises(SqlRejected) as unsupported:
            service.answer(UNSUPPORTED[0], alice, session)
        session.commit()
        reasons = set(session.scalars(select(SqlQuery.rejected_reason)).all())
    assert no_plan.value.reason == "NO_PLAN"
    assert unsupported.value.reason == "UNSUPPORTED_DIMENSION"
    assert {"NO_PLAN", "UNSUPPORTED_DIMENSION"} <= reasons


def test_the_agent_refuses_and_hands_off_without_status_buckets(
    client: TestClient,
) -> None:
    for question in UNSUPPORTED:
        response = client.post(
            "/api/v1/support/messages",
            json={"message": question},
            headers=ALICE,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["intent"] == "SELF_ANALYTICS", question
        assert "转人工" in payload["reply"], question
        assert payload["handoff"] is not None, question
        assert payload["task"] is None, question
        for bucket in ("已受理", "已到账", "状态待确认"):
            assert bucket not in payload["reply"], question
