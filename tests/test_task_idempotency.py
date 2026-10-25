"""Task creation under ``Idempotency-Key``: replay, not duplication.

The contract mirrors the refund execution ledger:

* same key + same request (and context) returns the first task, marked as a
  replay, and does not run the workflow twice;
* same key + a different request is a 409 — returning the old result would
  answer something the client did not ask;
* the key is scoped to its owner, so two customers may use the same string.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models.task import Task, TaskEvent
from tests.conftest import ALICE, BOB


def _post(
    client: TestClient,
    key: str | None,
    request: str,
    *,
    headers: dict[str, str] = ALICE,
    context: dict[str, object] | None = None,
) -> Any:
    sent = dict(headers)
    if key is not None:
        sent["Idempotency-Key"] = key
    return client.post(
        "/api/v1/tasks",
        headers=sent,
        json={"request": request, "context": context or {}},
    )


def _count(app: FastAPI, *, user_id: str = "alice") -> int:
    with app.state.session_factory() as session:
        return int(
            session.scalar(
                select(func.count(Task.id)).where(
                    Task.tenant_id == "demo", Task.user_id == user_id
                )
            )
            or 0
        )


def test_same_key_and_request_returns_the_first_task(
    client: TestClient, app: FastAPI
) -> None:
    request = "帮我查询订单 ORD-DEMO-1001"
    first = _post(client, "task-key-1", request)
    second = _post(client, "task-key-1", request)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["task_id"] == first.json()["task_id"]
    assert first.json()["idempotent_replay"] is False
    assert second.json()["idempotent_replay"] is True
    assert _count(app) == 1

    with app.state.session_factory() as session:
        created = session.scalar(
            select(func.count(TaskEvent.id)).where(
                TaskEvent.task_id == first.json()["task_id"],
                TaskEvent.event_type == "task.created",
            )
        )
    assert created == 1


def test_same_key_with_a_different_request_is_a_conflict(
    client: TestClient, app: FastAPI
) -> None:
    assert _post(client, "task-key-2", "帮我查询订单 ORD-DEMO-1001").status_code == 201
    response = _post(client, "task-key-2", "帮我查询订单 ORD-DEMO-1002")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert "different request" in response.json()["error"]["message"]
    assert _count(app) == 1


def test_same_key_with_a_different_context_is_a_conflict(
    client: TestClient,
) -> None:
    assert (
        _post(
            client,
            "task-key-3",
            "帮我查询订单 ORD-DEMO-1001",
            context={"channel": "web"},
        ).status_code
        == 201
    )
    response = _post(
        client,
        "task-key-3",
        "帮我查询订单 ORD-DEMO-1001",
        context={"channel": "email"},
    )

    assert response.status_code == 409


def test_the_key_is_scoped_to_its_owner(client: TestClient, app: FastAPI) -> None:
    request = "帮我查询订单 ORD-DEMO-1001"
    alice = _post(client, "shared-task-key", request, headers=ALICE)
    bob = _post(client, "shared-task-key", request, headers=BOB)

    assert alice.status_code == 201, alice.text
    assert bob.status_code == 201, bob.text
    assert alice.json()["task_id"] != bob.json()["task_id"]
    assert bob.json()["idempotent_replay"] is False
    assert _count(app, user_id="alice") == 1
    assert _count(app, user_id="bob") == 1


def test_no_key_still_creates_one_task_per_request(
    client: TestClient, app: FastAPI
) -> None:
    request = "帮我查询订单 ORD-DEMO-1001"
    first = _post(client, None, request)
    second = _post(client, None, request)

    assert first.json()["task_id"] != second.json()["task_id"]
    assert second.json()["idempotent_replay"] is False
    assert _count(app) == 2
