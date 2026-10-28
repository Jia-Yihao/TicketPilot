"""P6: task events are a live stream, not a one-shot replay.

The subscription reads the same ``task_events`` table every worker writes, so
these cases exercise the runtime generator directly (no model, no network):
replay, wait for an approval produced by a second caller, resume from
``Last-Event-ID``, heartbeat while quiet, and the old replay-only shape.
"""

from __future__ import annotations

import re
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import NotFoundError
from app.core.security import Identity
from tests.conftest import ALICE, REVIEWER

ACME = {"X-Tenant-Id": "acme", "X-User-Id": "someone", "X-Roles": "approver"}


def _refund_task(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={"request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "WAITING_APPROVAL"
    return body


def _alice() -> Identity:
    return Identity(tenant_id="demo", user_id="alice", roles=frozenset())


def test_the_stream_replays_then_pushes_an_approval_and_closes(
    app: FastAPI, client: TestClient
) -> None:
    task = _refund_task(client)
    task_id = task["task_id"]
    runtime = app.state.runtime
    stream = runtime.follow_events(
        _alice(), task_id, poll_interval=0.01, heartbeat=999, idle_timeout=5
    )

    def approve() -> None:
        time.sleep(0.1)
        response = client.post(
            f"/api/v1/tasks/{task_id}/approve",
            headers=REVIEWER,
            json={
                "approval_id": task["pending_approval"]["approval_id"],
                "decision": "APPROVE",
                "reason": "核对无误",
            },
        )
        assert response.status_code == 200, response.text

    worker = threading.Thread(target=approve)
    worker.start()
    try:
        events = list(stream)
    finally:
        worker.join(timeout=10)

    names = [event["event"] for event in events]
    assert "@" not in names, names
    assert names[0] == "task.created"
    assert "approval.approved" in names
    assert names[-1] in {"task.succeeded", "task.failed"}
    assert len({event["id"] for event in events}) == len(events)


def test_a_terminal_status_still_drains_the_last_event(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The status and final event commit together, but are read separately.

    A commit between the two reads used to close the stream before the final
    row was yielded. The generator must drain once more after seeing a
    terminal status.
    """
    runtime = app.state.runtime
    calls = {"events": 0}

    def fake_events(*args: object, **kwargs: object) -> list[dict]:
        calls["events"] += 1
        if calls["events"] == 1:
            return []
        return [
            {
                "id": "evt_last",
                "event": "task.succeeded",
                "data": {},
                "created_at": None,
            }
        ]

    monkeypatch.setattr(runtime, "events", fake_events)
    monkeypatch.setattr(
        runtime, "get_task", lambda *args, **kwargs: {"status": "SUCCEEDED"}
    )

    events = list(
        runtime.follow_events(
            _alice(), "task_1", poll_interval=0.01, heartbeat=999
        )
    )

    assert [event["event"] for event in events] == ["task.succeeded"]


def test_last_event_id_resumes_without_repeating(
    app: FastAPI, client: TestClient
) -> None:
    task = _refund_task(client)
    runtime = app.state.runtime
    stored = runtime.events(_alice(), task["task_id"])
    assert len(stored) >= 3
    anchor = stored[1]["id"]

    resumed = list(
        runtime.follow_events(
            _alice(),
            task["task_id"],
            after_id=anchor,
            poll_interval=0.01,
            heartbeat=999,
            idle_timeout=1,
        )
    )

    assert [event["id"] for event in resumed] == [
        event["id"] for event in stored[2:]
    ]


def test_a_quiet_open_task_emits_a_heartbeat(app: FastAPI, client: TestClient) -> None:
    task = _refund_task(client)
    runtime = app.state.runtime
    stream = runtime.follow_events(
        _alice(), task["task_id"], poll_interval=0.01, heartbeat=0.0, idle_timeout=5
    )
    try:
        seen_ping = False
        for _ in range(50):
            event = next(stream)
            if event["id"] is None:
                seen_ping = True
                assert event["event"] == "ping"
                break
        assert seen_ping
    finally:
        stream.close()


def test_a_terminal_task_closes_after_replaying(
    app: FastAPI, client: TestClient
) -> None:
    task = _refund_task(client)
    client.post(
        f"/api/v1/tasks/{task['task_id']}/cancel", headers=ALICE
    )
    runtime = app.state.runtime
    stored = runtime.events(_alice(), task["task_id"])
    streamed = list(
        runtime.follow_events(
            _alice(), task["task_id"], poll_interval=0.01, idle_timeout=5
        )
    )
    assert [event["id"] for event in streamed] == [
        event["id"] for event in stored
    ]


def test_follow_false_keeps_the_replay_only_shape(
    app: FastAPI, client: TestClient
) -> None:
    task = _refund_task(client)
    response = client.get(
        f"/api/v1/tasks/{task['task_id']}/events?follow=false", headers=ALICE
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    # The old shape is not just "some events": it is the same replay, in order.
    stored = app.state.runtime.events(_alice(), task["task_id"])
    assert stored, "the fixture task must have events, or the comparison is vacuous"
    frames = re.findall(r"^id: (\S+)\nevent: (\S+)$", response.text, flags=re.MULTILINE)
    assert frames == [(event["id"], event["event"]) for event in stored]


def test_the_stream_is_tenant_scoped(app: FastAPI, client: TestClient) -> None:
    task = _refund_task(client)
    runtime = app.state.runtime
    with pytest.raises(NotFoundError):
        list(
            runtime.follow_events(
                Identity("acme", "someone", frozenset()),
                task["task_id"],
                poll_interval=0.01,
            )
        )


def test_closing_a_stream_leaves_no_state_for_the_next(
    app: FastAPI, client: TestClient
) -> None:
    """The subscription lives in the generator, not in the runtime.

    An abandoned connection must not leave a marker behind: every later stream
    replays the same rows, and two concurrent streams do not share a cursor.
    """
    task = _refund_task(client)
    runtime = app.state.runtime
    stored = runtime.events(_alice(), task["task_id"])
    assert stored

    def open_stream():
        return runtime.follow_events(
            _alice(),
            task["task_id"],
            poll_interval=0.01,
            heartbeat=999,
            idle_timeout=0.05,
        )

    abandoned = open_stream()
    assert next(abandoned)["id"] == stored[0]["id"]
    abandoned.close()

    first = open_stream()
    second = open_stream()
    try:
        seen_first = [next(first)["id"] for _ in stored]
        seen_second = [next(second)["id"] for _ in stored]
    finally:
        first.close()
        second.close()

    expected = [event["id"] for event in stored]
    assert seen_first == expected
    assert seen_second == expected

    # After every earlier stream is closed, a fresh one starts at the top.
    again = list(open_stream())
    assert [event["id"] for event in again] == expected
