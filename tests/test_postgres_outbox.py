from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.security import Identity
from app.main import create_app
from app.models.approval import Approval
from app.models.enums import OutboxStatus
from app.models.outbox import OutboxMessage
from app.repositories.outbox import OutboxRepository
from app.seed import seed_all
from app.services.outbox import OutboxService

PG_URL = os.getenv("TICKETPILOT_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason="set TICKETPILOT_TEST_POSTGRES_URL to run the PostgreSQL outbox race",
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


def test_only_one_worker_claims_a_message() -> None:
    from psycopg import sql

    base = make_url(PG_URL)
    database = f"ticketpilot_obx_{uuid.uuid4().hex[:10]}"
    with _admin_connection(base, "postgres") as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database))
        )
    test_url = base.set(database=database).render_as_string(hide_password=False)

    application = None
    try:
        application = create_app(
            Settings(environment="test", database_url=test_url, auto_create_tables=True)
        )
        seed_all(application.state.session_factory)
        identity = Identity("demo", "alice", frozenset())

        with application.state.session_factory() as session:
            OutboxService(session).enqueue(
                identity=identity,
                aggregate_type="refund_task",
                aggregate_id="task_race",
                event_type="refund.pending_confirmation",
                dedupe_key="refund:task_race:pending_confirmation",
                payload={"task_id": "task_race"},
            )
            session.commit()

        with application.state.session_factory() as session:
            row_id = session.scalars(select(OutboxMessage)).one().id

        barrier = threading.Barrier(2)

        def claim(_index: int) -> bool:
            with application.state.session_factory() as session:
                barrier.wait()
                won = OutboxRepository(session).claim(row_id)
                # The claim is only a claim once it is committed; the
                # worker commits it before it pushes anything.
                session.commit()
                return won

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(claim, range(2)))

        assert sorted(outcomes) == [False, True]

        with application.state.session_factory() as session:
            row = session.scalars(select(OutboxMessage)).one()
            assert row.status == OutboxStatus.PROCESSING.value
    finally:
        if application is not None:
            application.state.engine.dispose()
        with _admin_connection(base, "postgres") as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(database)
                )
            )


def test_the_worker_settles_a_refund_on_postgres() -> None:
    from psycopg import sql

    base = make_url(PG_URL)
    database = f"ticketpilot_obxflow_{uuid.uuid4().hex[:10]}"
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
                mock_refund_mode="timeout_after",
            )
        )
        seed_all(application.state.session_factory)
        alice = Identity("demo", "alice", frozenset())
        reviewer = Identity("demo", "reviewer", frozenset({"approver"}))

        task = application.state.runtime.create_task(
            alice, "我要申请退款，订单 ORD-DEMO-1001，金额 129 元"
        )
        assert task["status"] == "WAITING_APPROVAL"

        with application.state.session_factory() as session:
            approval_id = session.scalars(select(Approval)).one().id

        application.state.runtime.approve(
            reviewer, task["task_id"], approval_id=approval_id, decision="APPROVE"
        )

        with application.state.session_factory() as session:
            row = session.scalars(select(OutboxMessage)).one()
            assert row.event_type == "refund.pending_confirmation"
            assert row.status == OutboxStatus.PENDING.value

        report = application.state.runtime.deliver_outbox()
        assert report.claimed == 1
        assert report.sent == 1

        with application.state.session_factory() as session:
            row = session.scalars(select(OutboxMessage)).one()
            assert row.status == OutboxStatus.SENT.value
            assert row.sent_at is not None

        settled = application.state.runtime.get_task(alice, task["task_id"])
        assert settled["result"]["outcome"] == "VERIFIED_SUCCESS"
    finally:
        if application is not None:
            application.state.engine.dispose()
        with _admin_connection(base, "postgres") as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(database)
                )
            )
