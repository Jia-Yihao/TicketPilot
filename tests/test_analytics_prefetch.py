"""P3-b prefetch: rows are scoped, sums are Python's, layers stay bounded.

These cases create their own rows so the expected numbers do not depend on
the demo seed. The equality case is the core promise: prefetch and controlled
SQL must return the same count, sum and buckets for the same identity.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.core.security import Identity
from app.models.commerce import Order, Refund
from app.models.enums import OrderStatus, RefundStatus
from app.sql.planner import HeuristicSqlPlanner
from app.sql.prefetch import PrefetchService
from app.sql.service import SqlAnalyticsService
from app.sql.snapshot import SnapshotStore

NOW = datetime(2026, 10, 25, 12, 0, 0)
ALICE = Identity(tenant_id="demo", user_id="alice", roles=frozenset())
BOB = Identity(tenant_id="demo", user_id="bob", roles=frozenset())
CAROL = Identity(tenant_id="acme", user_id="carol", roles=frozenset())
PLANNER = HeuristicSqlPlanner()


def _plan(question: str):  # type: ignore[no-untyped-def]
    plan = PLANNER.plan(question, now=NOW)
    assert plan is not None
    return plan


def _session(app: FastAPI) -> Session:
    return app.state.session_factory()


def _add_order(
    session: Session,
    order_id: str,
    *,
    tenant: str = "demo",
    user: str = "alice",
    status: OrderStatus = OrderStatus.DELIVERED,
    cents: int = 1000,
    created_at: datetime | None = None,
) -> None:
    moment = created_at or NOW - timedelta(days=1)
    session.add(
        Order(
            id=order_id,
            tenant_id=tenant,
            user_id=user,
            status=status,
            total_cents=cents,
            refundable_cents=cents,
            currency="CNY",
            paid_at=moment,
            created_at=moment,
            updated_at=moment,
        )
    )


def _add_refund(
    session: Session,
    refund_id: str,
    *,
    order_id: str = "ORD-A-1",
    tenant: str = "demo",
    user: str = "alice",
    status: RefundStatus = RefundStatus.SUCCEEDED,
    cents: int = 500,
    created_at: datetime | None = None,
) -> None:
    moment = created_at or NOW - timedelta(days=1)
    session.flush()
    session.add(
        Refund(
            id=refund_id,
            order_id=order_id,
            tenant_id=tenant,
            user_id=user,
            amount_cents=cents,
            status=status,
            idempotency_key=f"idem-{refund_id}",
            created_at=moment,
            updated_at=moment,
        )
    )


def test_prefetch_counts_only_the_callers_rows(app: FastAPI) -> None:
    with _session(app) as session:
        session.query(Order).delete()
        _add_order(session, "ORD-A-1", cents=1000)
        _add_order(session, "ORD-A-2", cents=2000)
        _add_order(session, "ORD-B-1", user="bob", cents=5000)
        _add_order(session, "ORD-C-1", tenant="acme", user="carol", cents=9000)
        session.commit()

        alice = PrefetchService().build(
            _plan("我买了多少订单"), ALICE, session, query_id="sql_alice", now=NOW
        )
        bob = PrefetchService().build(
            _plan("我买了多少订单"), BOB, session, query_id="sql_bob", now=NOW
        )
        carol = PrefetchService().build(
            _plan("我买了多少订单"), CAROL, session, query_id="sql_carol", now=NOW
        )
        dave = PrefetchService().build(
            _plan("我买了多少订单"),
            Identity("acme", "dave", frozenset()),
            session,
            query_id="sql_dave",
            now=NOW,
        )

    assert (alice.count, alice.total_cents) == (2, 3000)
    assert (bob.count, bob.total_cents) == (1, 5000)
    assert (carol.count, carol.total_cents) == (1, 9000)
    assert (dave.count, dave.total_cents) == (0, 0)


def test_prefetch_sums_money_in_python(app: FastAPI) -> None:
    with _session(app) as session:
        session.query(Order).delete()
        _add_order(session, "ORD-A-1", cents=12900)
        _add_order(session, "ORD-A-2", cents=8000)
        session.commit()
        snapshot = PrefetchService().build(
            _plan("我总共花了多少钱"), ALICE, session, query_id="sql_sum", now=NOW
        )
    assert snapshot.total_cents == 20900
    assert snapshot.count == 2


def test_prefetch_groups_by_status_and_keeps_sql_bucket_names(app: FastAPI) -> None:
    with _session(app) as session:
        session.query(Order).delete()
        _add_order(session, "ORD-A-1", status=OrderStatus.DELIVERED, cents=100)
        _add_order(session, "ORD-A-2", status=OrderStatus.SHIPPED, cents=200)
        session.commit()
        snapshot = PrefetchService().build(
            _plan("按状态统计我的订单笔数"),
            ALICE,
            session,
            query_id="sql_group",
            now=NOW,
        )
    assert [(group.bucket, group.count, group.total_cents) for group in snapshot.groups] == [
        ("DELIVERED", 1, 100),
        ("SHIPPED", 1, 200),
    ]


def test_prefetch_and_controlled_sql_agree_on_every_number(app: FastAPI) -> None:
    questions = (
        "我买了多少订单",
        "我总共花了多少钱",
        "我退了几笔款，多少钱",
        "按状态统计我的订单笔数",
    )
    with _session(app) as session:
        session.query(Refund).delete()
        session.query(Order).delete()
        _add_order(session, "ORD-A-1", status=OrderStatus.DELIVERED, cents=12900)
        _add_order(session, "ORD-A-2", status=OrderStatus.SHIPPED, cents=8000)
        _add_refund(session, "REF-A-1", cents=12900)
        session.commit()

        sql_service = SqlAnalyticsService(prefetch_enabled=False)
        for question in questions:
            sql_answer = sql_service.answer(question, ALICE, session)
            snapshot = PrefetchService().build(
                _plan(question), ALICE, session, query_id="sql_compare", now=NOW
            )
            assert snapshot.l1_rows() == sql_answer.rows, question


def test_a_long_result_is_truncated_and_says_so(app: FastAPI) -> None:
    with _session(app) as session:
        session.query(Order).delete()
        for index in range(5):
            _add_order(
                session,
                f"ORD-A-{index}",
                cents=100,
                created_at=NOW - timedelta(days=index + 1),
            )
        session.commit()
        snapshot = PrefetchService(max_rows=2).build(
            _plan("我买了多少订单，分别是哪几笔"),
            ALICE,
            session,
            query_id="sql_long",
            now=NOW,
        )
    assert snapshot.count == 5
    assert snapshot.total_cents == 500
    assert len(snapshot.rows) == 2
    assert snapshot.truncated is True
    assert "只展开最近 2 条" in snapshot.prompt_context()["facts"]["note"]


def test_the_time_range_is_applied_to_prefetch(app: FastAPI) -> None:
    with _session(app) as session:
        session.query(Order).delete()
        _add_order(session, "ORD-OLD", cents=100, created_at=NOW - timedelta(days=40))
        _add_order(session, "ORD-NEW", cents=200, created_at=NOW - timedelta(days=2))
        session.commit()
        snapshot = PrefetchService().build(
            _plan("最近 7 天我买了多少订单"),
            ALICE,
            session,
            query_id="sql_range",
            now=NOW,
        )
    assert (snapshot.count, snapshot.total_cents) == (1, 200)


def test_the_prompt_context_has_no_full_detail_and_fits_the_budget(
    app: FastAPI,
) -> None:
    with _session(app) as session:
        session.query(Order).delete()
        for index in range(20):
            _add_order(
                session,
                f"ORD-A-{index:02d}",
                cents=100 + index,
                created_at=NOW - timedelta(days=index),
            )
        session.commit()
        snapshot = PrefetchService(recent_limit=10, max_context_chars=400).build(
            _plan("我买了多少订单，分别是哪几笔"),
            ALICE,
            session,
            query_id="sql_budget",
            now=NOW,
        )
    context = snapshot.prompt_context()
    facts = context["facts"]
    assert "rows" not in facts
    assert len(facts["recent"]) < 10
    assert context["chars"] <= 400
    assert context["estimated_tokens"] <= 200


def test_snapshot_rows_are_only_returned_to_their_owner() -> None:
    from app.sql.snapshot import AnalyticsSnapshot

    snapshot = AnalyticsSnapshot(
        query_id="sql_owner",
        dataset="orders",
        label="订单",
        unit="单",
        metric="count",
        group_by=None,
        time_range="全部时间",
        count=1,
        total_cents=0,
        groups=(),
        recent=(),
        rows=({"id": "ORD-A-1"},),
        total_rows=1,
        truncated=False,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    store = SnapshotStore()
    store.put(snapshot, ALICE)
    assert store.get("sql_owner", ALICE, now=NOW) is snapshot
    assert store.get("sql_owner", BOB, now=NOW) is None
    assert store.get("sql_owner", ALICE, now=NOW + timedelta(minutes=6)) is None


def test_a_negative_ttl_is_not_silently_accepted() -> None:
    with pytest.raises(ValueError):
        PrefetchService(ttl_seconds=-1)


def test_the_service_routes_a_small_statistic_to_prefetch(app: FastAPI) -> None:
    from app.models.sql_query import SqlQuery

    with _session(app) as session:
        answer = SqlAnalyticsService().answer("我买了多少订单", ALICE, session)
        session.commit()
        row = session.get(SqlQuery, answer.query_id)

    assert answer.route == "PREFETCH"
    assert answer.sql == ""
    assert answer.snapshot is not None
    assert answer.rows == ({"metric": 2},)
    assert row is not None and row.status == "SUCCEEDED"
    assert row.sql_text == ""


def test_the_service_routes_a_grouped_statistic_to_sql(app: FastAPI) -> None:
    with _session(app) as session:
        answer = SqlAnalyticsService().answer(
            "按状态统计我的订单笔数", ALICE, session
        )
    assert answer.route == "SQL"
    assert answer.snapshot is None
    assert "FROM orders" in answer.sql


def test_the_trace_carries_the_route_and_reason(app: FastAPI) -> None:
    from app.observability.tracing import TraceRecorder

    trace = TraceRecorder(tenant_id="demo")
    with _session(app) as session:
        SqlAnalyticsService().answer(
            "我买了多少订单", ALICE, session, trace=trace
        )
    route = next(span for span in trace.spans if span.name == "analytics.route")
    assert route.attributes["route"] == "PREFETCH"
    assert route.attributes["route_reason"] == "SMALL_DATASET"
    assert route.attributes["row_count"] == 2
    prefetch = next(
        span for span in trace.spans if span.name == "analytics.prefetch"
    )
    assert prefetch.attributes["truncated"] is False
    assert prefetch.attributes["estimated_tokens"] > 0


def test_the_prefetch_route_keeps_the_tool_span_contract(app: FastAPI) -> None:
    """The support set and dashboards look for tool/analytics.query on both routes."""
    from app.observability.tracing import TraceRecorder

    trace = TraceRecorder(tenant_id="demo")
    with _session(app) as session:
        answer = SqlAnalyticsService().answer(
            "我买了多少订单", ALICE, session, trace=trace
        )
    span = next(
        item
        for item in trace.spans
        if item.kind == "tool" and item.name == "analytics.query"
    )
    assert span.attributes["route"] == "PREFETCH"
    assert span.attributes["query_id"] == answer.query_id
    assert span.attributes["dataset"] == "orders"


def test_the_api_reports_the_route(app: FastAPI, client) -> None:  # type: ignore[no-untyped-def]
    count = client.post(
        "/api/v1/analytics/query",
        json={"question": "我买了多少订单"},
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    grouped = client.post(
        "/api/v1/analytics/query",
        json={"question": "按状态统计我的订单笔数"},
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert count.status_code == 200
    assert count.json()["route"] == "PREFETCH"
    assert count.json()["sql"] == ""
    assert grouped.status_code == 200
    assert grouped.json()["route"] == "SQL"


def test_turning_prefetch_off_restores_the_sql_path_in_the_app(
    app_factory,  # type: ignore[no-untyped-def]
) -> None:
    from fastapi.testclient import TestClient

    application = app_factory(analytics_prefetch_enabled=False)
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/analytics/query",
            json={"question": "我买了多少订单"},
            headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        )
    assert response.status_code == 200
    assert response.json()["route"] == "SQL"
    assert "FROM orders" in response.json()["sql"]


def test_the_analytics_bookkeeping_spans_do_not_block_replay() -> None:
    """Routing and budget records have no side effects; replay must keep them."""
    from evals.replay import RecordedTurn, _ineligible_reason

    spans = {
        "trc_replay": [
            {"kind": "tool", "name": "analytics.route"},
            {"kind": "tool", "name": "analytics.query"},
            {"kind": "tool", "name": "analytics.prefetch"},
            {"kind": "tool", "name": "analytics.expand"},
            {"kind": "generate", "name": "support.reply"},
        ]
    }
    turns = [RecordedTurn(message="我买了多少订单", trace_id="trc_replay")]
    assert _ineligible_reason(turns, spans) is None


def test_over_threshold_groups_match_the_python_path(app: FastAPI) -> None:
    from app.models.enums import OrderStatus

    with _session(app) as session:
        session.query(Order).delete()
        _add_order(session, "ORD-G-1", status=OrderStatus.DELIVERED, cents=1000)
        _add_order(session, "ORD-G-2", status=OrderStatus.DELIVERED, cents=2000)
        _add_order(session, "ORD-G-3", status=OrderStatus.SHIPPED, cents=500)
        session.commit()

        plan = _plan("按状态统计我的订单笔数")
        over = PrefetchService(max_rows=2).build(
            plan, ALICE, session, query_id="sql_sql_groups", now=NOW
        )
        under = PrefetchService(max_rows=200).build(
            plan, ALICE, session, query_id="sql_python_groups", now=NOW
        )

    assert over.truncated is True
    assert over.count == under.count == 3
    assert over.groups == under.groups
    assert len(over.rows) == 2


def test_storing_a_new_snapshot_purges_expired_ones() -> None:
    from app.sql.snapshot import AnalyticsSnapshot

    def _snapshot(query_id: str, expires_at: datetime) -> AnalyticsSnapshot:
        return AnalyticsSnapshot(
            query_id=query_id,
            dataset="orders",
            label="订单",
            unit="单",
            metric="count",
            group_by=None,
            time_range="全部时间",
            count=1,
            total_cents=0,
            groups=(),
            recent=(),
            rows=({"id": "ORD-A-1"},),
            total_rows=1,
            truncated=False,
            created_at=NOW,
            expires_at=expires_at,
        )

    store = SnapshotStore()
    store.put(_snapshot("sql_stale", NOW), ALICE)
    store.put(_snapshot("sql_fresh", NOW + timedelta(minutes=5)), ALICE)
    assert len(store._items) == 1
    assert store.get("sql_stale", ALICE, now=NOW + timedelta(minutes=1)) is None
