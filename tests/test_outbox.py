from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import Identity
from app.core.time import utcnow
from app.models.enums import OutboxStatus
from app.models.outbox import OutboxMessage
from app.services.outbox import OutboxService
from tests.conftest import ALICE, REVIEWER

CAROL_APPROVER = {"X-Tenant-Id": "acme", "X-User-Id": "carol", "X-Roles": "approver"}


def _request_refund(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={"request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approve(client: TestClient, task_id: str) -> None:
    response = client.post(
        f"/api/v1/tasks/{task_id}/approve",
        headers=REVIEWER,
        json={"decision": "APPROVE"},
    )
    assert response.status_code == 200, response.text


def _messages(app: FastAPI) -> list[OutboxMessage]:
    with app.state.session_factory() as session:
        return list(
            session.scalars(select(OutboxMessage).order_by(OutboxMessage.created_at))
        )


def _enqueue(session, identity: Identity, *, dedupe_key: str = "refund:t1:pending"):
    return OutboxService(session).enqueue(
        identity=identity,
        aggregate_type="refund_task",
        aggregate_id="t1",
        event_type="refund.pending_confirmation",
        dedupe_key=dedupe_key,
        payload={"task_id": "t1"},
    )


def test_the_same_fact_is_queued_once(app: FastAPI) -> None:
    identity = Identity("demo", "alice", frozenset())
    with app.state.session_factory() as session:
        first = _enqueue(session, identity)
        second = _enqueue(session, identity)
        session.commit()
        assert first.id == second.id

    assert len(_messages(app)) == 1


def test_the_message_is_written_with_the_callers_transaction(app: FastAPI) -> None:
    identity = Identity("demo", "alice", frozenset())
    factory = app.state.session_factory

    with factory() as session:
        _enqueue(session, identity)
        session.flush()
        with factory() as other:
            assert list(other.scalars(select(OutboxMessage))) == []
        session.rollback()

    with factory() as session:
        assert list(session.scalars(select(OutboxMessage))) == []


def test_a_delivered_message_is_marked_sent(app: FastAPI) -> None:
    identity = Identity("demo", "alice", frozenset())
    with app.state.session_factory() as session:
        _enqueue(session, identity)
        session.commit()

    with app.state.session_factory() as session:
        delivered: list[str] = []
        report = OutboxService(session).deliver(lambda row: delivered.append(row.id))
        assert report.claimed == 1
        assert report.sent == 1
        assert report.failed == 0
        assert report.dead_lettered == 0
        assert len(delivered) == 1

    row = _messages(app)[0]
    assert row.status == OutboxStatus.SENT.value
    assert row.sent_at is not None


def test_a_message_that_never_lands_becomes_a_dead_letter(app: FastAPI) -> None:
    identity = Identity("demo", "alice", frozenset())
    with app.state.session_factory() as session:
        _enqueue(session, identity)
        session.commit()

    with app.state.session_factory() as session:
        service = OutboxService(session, max_attempts=1)

        def sink(_row: OutboxMessage) -> None:
            raise RuntimeError("no external system")

        report = service.deliver(sink)
        assert report.dead_lettered == 1
        assert report.failed == 0

    row = _messages(app)[0]
    assert row.status == OutboxStatus.FAILED.value
    assert row.retry_count == 1
    assert "no external system" in (row.last_error or "")


def test_a_failed_push_waits_for_the_next_pass(app: FastAPI) -> None:
    identity = Identity("demo", "alice", frozenset())
    with app.state.session_factory() as session:
        _enqueue(session, identity)
        session.commit()

    with app.state.session_factory() as session:
        service = OutboxService(session, max_attempts=5, backoff_seconds=60)

        def sink(_row: OutboxMessage) -> None:
            raise RuntimeError("the push did not land")

        report = service.deliver(sink)
        assert report.failed == 1
        assert report.dead_lettered == 0

    row = _messages(app)[0]
    assert row.status == OutboxStatus.PENDING.value
    assert row.retry_count == 1
    assert row.available_at > utcnow()


def test_a_refund_nobody_knew_the_outcome_of_leaves_a_message(app_factory) -> None:
    app = app_factory(mock_refund_mode="timeout_after")
    client = TestClient(app)
    created = _request_refund(client)
    _approve(client, created["task_id"])

    messages = _messages(app)
    assert len(messages) == 1
    message = messages[0]
    assert message.event_type == "refund.pending_confirmation"
    assert message.aggregate_type == "refund_task"
    assert message.aggregate_id == created["task_id"]
    assert message.status == OutboxStatus.PENDING.value
    assert message.payload["task_id"] == created["task_id"]


def test_the_worker_settles_the_refund_behind_the_message(app_factory) -> None:
    app = app_factory(mock_refund_mode="timeout_after")
    client = TestClient(app)
    created = _request_refund(client)
    _approve(client, created["task_id"])

    report = app.state.runtime.deliver_outbox()
    assert report.claimed == 1
    assert report.sent == 1
    assert report.failed == 0

    task = app.state.runtime.get_task(
        Identity("demo", "alice", frozenset()), created["task_id"]
    )
    assert task["result"]["outcome"] == "VERIFIED_SUCCESS"
    assert _messages(app)[0].status == OutboxStatus.SENT.value


def test_a_refund_that_never_lands_is_waited_for_and_then_given_up_on(
    app_factory,
) -> None:
    app = app_factory(mock_refund_mode="timeout_before")
    client = TestClient(app)
    created = _request_refund(client)
    _approve(client, created["task_id"])

    report = app.state.runtime.deliver_outbox()
    assert report.claimed == 1
    assert report.sent == 0
    assert report.failed == 1

    message = _messages(app)[0]
    assert message.status == OutboxStatus.PENDING.value
    assert message.retry_count == 1
    assert "still unconfirmed" in (message.last_error or "")


def test_the_seat_sees_the_queue_and_the_customer_does_not(app_factory) -> None:
    app = app_factory(mock_refund_mode="timeout_after")
    client = TestClient(app)
    created = _request_refund(client)
    _approve(client, created["task_id"])

    assert client.get("/api/v1/outbox", headers=ALICE).status_code == 403

    listing = client.get("/api/v1/outbox", headers=REVIEWER)
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["event_type"] == "refund.pending_confirmation"
    assert body["items"][0]["status"] == "PENDING"

    other_tenant = client.get("/api/v1/outbox", headers=CAROL_APPROVER)
    assert other_tenant.status_code == 200
    assert other_tenant.json()["total"] == 0


def test_the_seat_can_run_a_delivery_pass(app_factory) -> None:
    app = app_factory(mock_refund_mode="timeout_after")
    client = TestClient(app)
    created = _request_refund(client)
    _approve(client, created["task_id"])

    assert client.post("/api/v1/outbox/deliver", headers=ALICE).status_code == 403

    response = client.post("/api/v1/outbox/deliver", headers=REVIEWER)
    assert response.status_code == 200
    assert response.json() == {
        "claimed": 1,
        "sent": 1,
        "failed": 0,
        "dead_lettered": 0,
    }
