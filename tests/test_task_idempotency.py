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


def test_a_retry_keeps_the_same_digest_because_the_key_never_enters_context(
    client: TestClient, app: FastAPI
) -> None:
    """R9 口径 §5.6 之三 / §2.4 第 4 条：关联键只能走列。

    `request_digest` 由 `canonical_digest({"request": request, "context": context or {}})`
    现算（`app/services/runtime.py:312-316`）。若把 `conversation_id` 塞进 `context`，
    第二次请求的摘要与第一次不等，`:329-332` 会抛 `ConflictError`——同一幂等键的
    重试就从「返回第一个任务」变成 409。这条断言同时钉住列本身：直接调 API 建单
    的 `conversation_id` 必须是 `NULL`（§2.4 第 3 条），且不能出现在 `context` 里。
    """
    request = "帮我查询订单 ORD-DEMO-1001"
    first = _post(client, "task-key-digest", request)
    assert first.status_code == 201, first.text

    with app.state.session_factory() as session:
        created = session.get(Task, first.json()["task_id"])
        assert created is not None
        digest_after_first = created.request_digest
    assert digest_after_first

    second = _post(client, "task-key-digest", request)
    assert second.status_code == 201, second.text
    assert second.json()["task_id"] == first.json()["task_id"]
    assert second.json()["idempotent_replay"] is True
    assert _count(app) == 1

    with app.state.session_factory() as session:
        task = session.get(Task, first.json()["task_id"])
        assert task is not None
        rows = len(
            session.scalars(
                select(Task.id).where(
                    Task.tenant_id == "demo",
                    Task.user_id == "alice",
                    Task.idempotency_key == "task-key-digest",
                )
            ).all()
        )
    # 重试没有改写摘要——摘要一变即 409（§2.4 第 4 条），所以「与第一次相同」
    # 这条读数就是「键没进 context」的证据。
    assert task.request_digest == digest_after_first
    assert rows == 1
    assert "conversation_id" not in task.context
    assert task.conversation_id is None
