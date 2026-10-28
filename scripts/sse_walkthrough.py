"""Walk one refund task's event stream from creation to terminal state.

Two checks, both over the real HTTP endpoint (no model, no network):

1. **Live push** — keep the connection open after the replay, approve the
   refund from a second thread, and watch ``approval.approved`` /
   ``task.succeeded`` arrive without reconnecting.
2. **Resume** — open a second task, disconnect after ``approval.requested``,
   let the approval finish while nobody is listening, then reconnect with
   ``Last-Event-ID`` and confirm only the missed events are replayed.

Run::

    python scripts/sse_walkthrough.py

The script uses a temporary SQLite database and prints one line per event, so
its output is the evidence for the P6 acceptance criteria.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.seed import seed_all

ALICE = {"X-Tenant-Id": "demo", "X-User-Id": "alice"}
REVIEWER = {"X-Tenant-Id": "demo", "X-User-Id": "reviewer", "X-Roles": "approver"}
TERMINAL_EVENTS = {
    "task.succeeded",
    "task.failed",
    "task.cancelled",
    "task.timed_out",
    "task.rejected",
    "task.expired",
}


def parse_frame(block: list[str]) -> dict[str, Any] | None:
    event = ""
    event_id = ""
    data_lines: list[str] = []
    for line in block:
        if not line or line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "id":
            event_id = value
        elif field == "event":
            event = value
        elif field == "data":
            data_lines.append(value)
    if not event:
        return None
    data = json.loads("\n".join(data_lines)) if data_lines else {}
    return {"id": event_id, "event": event, "data": data}


def iter_events(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    block: list[str] = []
    for line in lines:
        if line == "":
            frame = parse_frame(block)
            block = []
            if frame is not None:
                yield frame
        else:
            block.append(line)


def create_refund(
    client: TestClient, order_id: str, amount_yuan: int
) -> dict[str, Any]:
    request = f"我要申请退款，订单 {order_id}，金额 {amount_yuan} 元"
    response = client.post(
        "/api/v1/tasks", headers=ALICE, json={"request": request}
    )
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["status"] == "WAITING_APPROVAL", task["status"]
    return task


def approve(client: TestClient, task: dict[str, Any]) -> None:
    response = client.post(
        f"/api/v1/tasks/{task['task_id']}/approve",
        headers=REVIEWER,
        json={
            "approval_id": task["pending_approval"]["approval_id"],
            "decision": "APPROVE",
            "reason": "sse walkthrough",
        },
    )
    assert response.status_code == 200, response.text


def stream_url(task_id: str) -> str:
    return f"/api/v1/tasks/{task_id}/events"


def print_event(prefix: str, event: dict[str, Any], started: float) -> None:
    elapsed = time.monotonic() - started
    print(f"{prefix} +{elapsed:5.2f}s  {event['event']:<22} {event['id']}")


def live_push(listener: TestClient, actor: TestClient) -> list[str]:
    task = create_refund(listener, "ORD-DEMO-1001", 129)
    names: list[str] = []
    event_ids: list[str] = []
    started = time.monotonic()
    worker: threading.Thread | None = None

    def approve_later() -> None:
        time.sleep(0.2)
        approve(actor, task)

    with listener.stream(
        "GET", stream_url(task["task_id"]), headers=ALICE
    ) as response:
        assert response.status_code == 200, response.text
        print(f"[live] stream open: {response.headers['content-type']}")
        for event in iter_events(response.iter_lines()):
            print_event("[live]", event, started)
            names.append(event["event"])
            event_ids.append(event["id"])
            if event["event"] == "approval.requested" and worker is None:
                worker = threading.Thread(target=approve_later, daemon=True)
                worker.start()
            if event["event"] in TERMINAL_EVENTS:
                break
    if worker is not None:
        worker.join(timeout=10)

    assert names[0] == "task.created", names
    assert "approval.approved" in names, names
    assert names[-1] == "task.succeeded", names
    assert len(set(event_ids)) == len(event_ids), "duplicate event ids"
    print(f"[live] ok: {len(names)} events, live push after approval.requested")
    return names


def resume_after_disconnect(listener: TestClient, actor: TestClient) -> None:
    task = create_refund(listener, "ORD-DEMO-1002", 80)
    seen: list[dict[str, Any]] = []
    started = time.monotonic()
    with listener.stream(
        "GET", stream_url(task["task_id"]), headers=ALICE
    ) as response:
        for event in iter_events(response.iter_lines()):
            print_event("[drop]", event, started)
            seen.append(event)
            if event["event"] == "approval.requested":
                break

    # The connection is closed. The approval lands while nobody is listening.
    approve(actor, task)
    last_id = seen[-1]["id"]

    resumed: list[dict[str, Any]] = []
    started = time.monotonic()
    with actor.stream(
        "GET",
        stream_url(task["task_id"]),
        headers={**ALICE, "Last-Event-ID": last_id},
    ) as response:
        for event in iter_events(response.iter_lines()):
            print_event("[resume]", event, started)
            resumed.append(event)
            if event["event"] == "task.succeeded":
                break

    seen_ids = {event["id"] for event in seen}
    assert resumed, "resume produced no events"
    assert resumed[0]["id"] != last_id, "resume repeated the anchor event"
    assert all(event["id"] not in seen_ids for event in resumed)
    assert resumed[-1]["event"] == "task.succeeded", resumed[-1]["event"]
    print(f"[resume] ok: {len(resumed)} missed events replayed after {last_id}")


def main() -> int:
    with TemporaryDirectory() as tmp:
        settings = Settings(
            environment="test",
            database_url=f"sqlite+pysqlite:///{Path(tmp) / 'sse-walkthrough.db'}",
            auto_create_tables=True,
            mock_refund_mode="success",
        )
        app = create_app(settings)
        seed_all(app.state.session_factory)
        try:
            with TestClient(app) as listener, TestClient(app) as actor:
                live_push(listener, actor)
                resume_after_disconnect(listener, actor)
        finally:
            app.state.engine.dispose()
    print("P6 walkthrough passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
