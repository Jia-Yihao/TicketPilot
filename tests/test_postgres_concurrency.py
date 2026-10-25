from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.errors import ConflictError
from app.core.security import Identity
from app.main import create_app
from app.models.approval import Approval
from app.models.commerce import Order, Refund
from app.models.enums import ApprovalStatus
from app.seed import seed_all

PG_URL = os.getenv("TICKETPILOT_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason="set TICKETPILOT_TEST_POSTGRES_URL to run the PostgreSQL race regression",
)


def _admin_connection(url, database: str):
    import psycopg

    return psycopg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        dbname=database,
        autocommit=True,
    )


def test_concurrent_approvals_produce_exactly_one_refund() -> None:
    from psycopg import sql

    base = make_url(PG_URL)
    database = f"ticketpilot_race_{uuid.uuid4().hex[:10]}"
    with _admin_connection(base, "postgres") as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database))
        )
    test_url = base.set(database=database).render_as_string(hide_password=False)

    application = None
    try:
        application = create_app(
            Settings(
                environment="test",
                database_url=test_url,
                auto_create_tables=True,
                mock_refund_delay_seconds=0.5,
            )
        )
        seed_all(application.state.session_factory)
        alice = Identity("demo", "alice", frozenset())
        request = "我要申请退款，订单 ORD-DEMO-1001，金额 129 元"

        task = application.state.runtime.create_task(alice, request)
        assert task["status"] == "WAITING_APPROVAL"

        duplicate = application.state.runtime.create_task(alice, request)
        assert duplicate["status"] == "FAILED"
        assert "open approval" in duplicate["result"]["summary"]

        with application.state.session_factory() as session:
            approvals = list(session.scalars(select(Approval)))
            assert len(approvals) == 1
            approval_id = approvals[0].id

        barrier = threading.Barrier(2)

        def review(index: int):
            reviewer = Identity("demo", f"reviewer-{index}", frozenset({"approver"}))
            barrier.wait()
            try:
                return application.state.runtime.approve(
                    reviewer,
                    task["task_id"],
                    approval_id=approval_id,
                    decision="APPROVE",
                )
            except Exception as exc:  # noqa: BLE001  (returned to the assertion below)
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(review, range(2)))

        successes = [item for item in results if isinstance(item, dict)]
        failures = [item for item in results if isinstance(item, Exception)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], ConflictError)
        assert successes[0]["status"] == "SUCCEEDED"
        assert successes[0]["result"]["outcome"] == "VERIFIED_SUCCESS"
        assert successes[0]["latest_approval"]["status"] == "CONSUMED"

        with application.state.session_factory() as session:
            refunds = list(session.scalars(select(Refund)))
            approval = session.get(Approval, approval_id)
            order = session.get(Order, "ORD-DEMO-1001")
        assert len(refunds) == 1
        assert refunds[0].amount_cents == 12900
        assert approval.status is ApprovalStatus.CONSUMED
        assert order.refundable_cents == 0
        assert order.version == 2

        with application.state.engine.connect() as connection:
            definition = connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname = 'uq_approvals_open_per_resource'"
                )
            ).scalar()
        assert definition is not None
        assert "UNIQUE" in definition
        assert "PENDING" in definition
        assert "APPROVED" in definition
    finally:
        if application is not None:
            application.state.engine.dispose()
        with _admin_connection(base, "postgres") as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(database)
                )
            )
