"""The controlled text-to-SQL path, end to end.

The planner writes down what was asked, the builder turns that into a
statement, the guard decides whether it may run, and the ledger keeps the
outcome. These tests walk that path: the numbers, the refusals and the
scope.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import Identity
from app.models.sql_query import SqlQuery
from app.sql.errors import SqlRejected
from app.sql.planner import HeuristicSqlPlanner
from app.sql.service import SqlAnalyticsService

NOW = datetime(2026, 10, 23, 12, 0, 0)

ALICE = Identity(tenant_id="demo", user_id="alice", roles=frozenset())
BOB = Identity(tenant_id="demo", user_id="bob", roles=frozenset())


@pytest.fixture
def planner() -> HeuristicSqlPlanner:
    return HeuristicSqlPlanner()


@pytest.fixture
def service(app: FastAPI) -> SqlAnalyticsService:
    return SqlAnalyticsService(max_limit=200)


def _session(app: FastAPI):  # type: ignore[no-untyped-def]
    return app.state.session_factory()


# ----------------------------------------------------------------- the plan
def test_a_count_question_becomes_a_plan(planner: HeuristicSqlPlanner) -> None:
    plan = planner.plan("我买了多少订单", now=NOW)
    assert plan is not None
    assert plan.dataset == "orders"
    assert plan.metric == "count"


def test_a_sum_question_becomes_a_plan(planner: HeuristicSqlPlanner) -> None:
    plan = planner.plan("我总共花了多少钱", now=NOW)
    assert plan is not None
    assert plan.dataset == "orders"
    assert plan.metric == "sum"


def test_a_question_with_both_becomes_both(planner: HeuristicSqlPlanner) -> None:
    plan = planner.plan("我退了几笔款，多少钱", now=NOW)
    assert plan is not None
    assert plan.metric == "count_and_sum"


def test_a_distribution_question_becomes_a_plan(planner: HeuristicSqlPlanner) -> None:
    plan = planner.plan("按月统计我的退款笔数", now=NOW)
    assert plan is not None
    assert plan.group_by == "month"


def test_a_status_question_becomes_a_plan(planner: HeuristicSqlPlanner) -> None:
    plan = planner.plan("我退款失败了多少笔", now=NOW)
    assert plan is not None
    assert plan.dataset == "refunds"
    assert plan.status == "FAILED"


def test_a_status_of_another_record_set_is_refused(
    planner: HeuristicSqlPlanner,
) -> None:
    """'已签收' is an order status; a refund question cannot use it."""
    assert planner.plan("我的退款有多少笔已签收", now=NOW) is None


def test_an_unknown_question_has_no_plan(planner: HeuristicSqlPlanner) -> None:
    assert planner.plan("今天天气怎么样", now=NOW) is None
    assert planner.plan("我买了多少订单", now=NOW) is not None


def test_last_month_keeps_the_month_boundaries(
    planner: HeuristicSqlPlanner,
) -> None:
    plan = planner.plan("我上个月退了几笔款", now=NOW)
    assert plan is not None
    assert plan.time_range is not None
    assert plan.time_range.start == datetime(2026, 9, 1)
    assert plan.time_range.end == datetime(2026, 10, 1)


def test_a_month_boundary_is_clamped(planner: HeuristicSqlPlanner) -> None:
    """A 31st shifts to the end of a shorter month, not into the next one."""
    plan = planner.plan("我上个月退了几笔款", now=datetime(2026, 3, 31, 9, 0))
    assert plan is not None
    assert plan.time_range is not None
    assert plan.time_range.start == datetime(2026, 2, 1)
    assert plan.time_range.end == datetime(2026, 3, 1)


def test_recent_days_are_capped(planner: HeuristicSqlPlanner) -> None:
    plan = planner.plan("最近400天我的退款状态分布", now=NOW)
    assert plan is not None
    assert plan.time_range is not None
    assert plan.time_range.label == "最近 365 天"


# --------------------------------------------------------------- the answer
def test_the_answer_counts_only_my_own_records(
    app: FastAPI, service: SqlAnalyticsService
) -> None:
    with _session(app) as session:
        answer = service.answer("我买了多少订单", ALICE, session)
    assert answer.row_count == 1
    assert answer.rows[0]["metric"] == 2


def test_another_customer_sees_another_number(
    app: FastAPI, service: SqlAnalyticsService
) -> None:
    with _session(app) as session:
        answer = service.answer("我买了多少订单", BOB, session)
    assert answer.rows[0]["metric"] == 1


def test_the_answer_carries_the_statement(
    app: FastAPI, service: SqlAnalyticsService
) -> None:
    with _session(app) as session:
        answer = service.answer("我买了多少订单", ALICE, session)
    assert "FROM orders" in answer.sql
    assert "tenant_id" in answer.sql
    assert "user_id" in answer.sql
    assert "LIMIT" in answer.sql


def test_the_statement_binds_its_values(app: FastAPI, service: SqlAnalyticsService) -> None:
    """No value is pasted into the statement text."""
    with _session(app) as session:
        answer = service.answer("我上个月退了几笔款", ALICE, session)
    assert ":start" in answer.sql
    assert ":end" in answer.sql
    assert "2026-09" not in answer.sql


def test_a_sum_answer_adds_up_the_money(
    app: FastAPI, service: SqlAnalyticsService
) -> None:
    with _session(app) as session:
        answer = service.answer("我总共花了多少钱", ALICE, session)
    assert answer.rows[0]["metric_total"] == 20900


def test_an_unknown_question_is_refused(
    app: FastAPI, service: SqlAnalyticsService
) -> None:
    with _session(app) as session:
        with pytest.raises(SqlRejected) as caught:
            service.answer("今天天气怎么样", ALICE, session)
    assert caught.value.reason == "NO_PLAN"


def test_the_ledger_records_the_success(
    app: FastAPI, service: SqlAnalyticsService
) -> None:
    with _session(app) as session:
        answer = service.answer("我买了多少订单", ALICE, session)
        session.commit()
        row = session.get(SqlQuery, answer.query_id)
    assert row is not None
    assert row.status == "SUCCEEDED"
    assert row.dataset == "orders"
    assert row.metric == "count"
    assert row.row_count == 1


def test_the_ledger_records_the_refusal(
    app: FastAPI, service: SqlAnalyticsService
) -> None:
    with _session(app) as session:
        with pytest.raises(SqlRejected):
            service.answer("今天天气怎么样", ALICE, session)
        session.commit()
        row = (
            session.query(SqlQuery)
            .filter(SqlQuery.rejected_reason == "NO_PLAN")
            .one()
        )
    assert row.status == "REJECTED"


def test_the_ledger_keeps_the_values_out(
    app: FastAPI, service: SqlAnalyticsService
) -> None:
    """The parameter values are reduced to a fingerprint."""
    with _session(app) as session:
        answer = service.answer("我上个月退了几笔款", ALICE, session)
        session.commit()
        row = session.get(SqlQuery, answer.query_id)
    assert row is not None
    assert len(row.params_digest) == 64
    assert "2026-09" not in row.params_digest
    assert "alice" not in row.params_digest


def test_the_ledger_keeps_the_window(
    app: FastAPI, service: SqlAnalyticsService
) -> None:
    with _session(app) as session:
        answer = service.answer("我上个月退了几笔款", ALICE, session)
        session.commit()
        row = session.get(SqlQuery, answer.query_id)
    assert row is not None
    assert row.time_range == "上个月"


# -------------------------------------------------------------------- the API
def test_the_api_answers_a_question(client: TestClient) -> None:
    response = client.post(
        "/api/v1/analytics/query",
        json={"question": "我买了多少订单"},
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["plan"]["dataset"] == "orders"
    assert payload["rows"][0]["metric"] == 2
    assert payload["row_count"] == 1


def test_the_api_refuses_an_unknown_question(client: TestClient) -> None:
    response = client.post(
        "/api/v1/analytics/query",
        json={"question": "今天天气怎么样"},
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "SQL_REFUSED"
    assert error["details"]["reason"] == "NO_PLAN"


def test_the_api_needs_an_identity(client: TestClient) -> None:
    response = client.post("/api/v1/analytics/query", json={"question": "我买了多少订单"})
    assert response.status_code == 401


def test_the_api_lists_the_queries(client: TestClient) -> None:
    client.post(
        "/api/v1/analytics/query",
        json={"question": "我买了多少订单"},
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    response = client.get(
        "/api/v1/analytics/queries",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["dataset"] == "orders"


def test_another_customer_sees_no_queries(client: TestClient) -> None:
    client.post(
        "/api/v1/analytics/query",
        json={"question": "我买了多少订单"},
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    response = client.get(
        "/api/v1/analytics/queries",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "bob"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_another_customers_query_is_not_found(client: TestClient) -> None:
    created = client.post(
        "/api/v1/analytics/query",
        json={"question": "我买了多少订单"},
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    query_id = created.json()["query_id"]
    response = client.get(
        f"/api/v1/analytics/queries/{query_id}",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "bob"},
    )
    assert response.status_code == 404


def test_a_seat_sees_the_whole_tenant(client: TestClient) -> None:
    client.post(
        "/api/v1/analytics/query",
        json={"question": "我买了多少订单"},
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    response = client.get(
        "/api/v1/analytics/queries",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "seat", "X-Roles": "support"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


# ------------------------------------------------------------------- the agent
def test_the_agent_answers_with_the_analytics_card(client: TestClient) -> None:
    response = client.post(
        "/api/v1/support/messages",
        json={"message": "我买了多少订单"},
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "SELF_ANALYTICS"
    assert "2" in payload["reply"]
    card = payload["context_cards"][0]
    assert card["kind"] == "analytics"
    assert card["fields"]["records"] == 2


def test_the_agent_refuses_what_it_cannot_answer(client: TestClient) -> None:
    """A status of another record set is refused, and a human takes over."""
    response = client.post(
        "/api/v1/support/messages",
        json={"message": "我的订单有多少笔已到账"},
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert "转人工" in payload["reply"]
    assert payload["handoff"] is not None


def test_a_grouped_count_names_the_bucket(client: TestClient) -> None:
    response = client.post(
        "/api/v1/support/messages",
        json={"message": "按状态统计我的订单笔数"},
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "SELF_ANALYTICS"
    card = payload["context_cards"][0]
    assert card["kind"] == "analytics"
    assert card["fields"]["已签收"] == "1 单"
    assert card["fields"]["已发货"] == "1 单"


def test_a_grouped_sum_reports_the_money(client: TestClient) -> None:
    """A grouped sum shows the amount, not a count of zero."""
    response = client.post(
        "/api/v1/support/messages",
        json={"message": "按状态统计我一共花了多少钱"},
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "SELF_ANALYTICS"
    assert "0 单" not in payload["reply"]
    card = payload["context_cards"][0]
    assert card["fields"]["已签收_amount_cents"] == 12900
    assert card["fields"]["已发货_amount_cents"] == 8000
