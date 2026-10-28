"""The cross-worker model ledger: one row per call, readable by a seat.

The in-process ``LlmUsage`` counter only proves what one worker spent. These
tests pin the durable fact instead:

1. a task planned by the model writes a ``llm_usage`` row in the same
   transaction as ``task.routed``, including the validation retry count;
2. the chat gap path writes its own row, labelled ``source=chat``;
3. a seat reads the tenant's totals through the API; a customer cannot, and
   another tenant's rows never appear.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent.pydantic_agent import ModelTrace
from app.agent.routing import RoutePlan
from app.core.config import Settings
from app.main import create_app
from app.models.enums import Intent
from app.models.llm_usage import LlmUsageRecord
from app.models.task import TaskEvent
from app.seed import seed_all
from app.services.llm_usage import LlmUsageService
from tests.conftest import ALICE, CAROL, REVIEWER


class RetryingBrain:
    """A model-backed brain whose run needed a second request to validate."""

    def __init__(self, intent: Intent = Intent.UNKNOWN) -> None:
        self.intent = intent
        self.requests: list[str] = []

    @property
    def model_name(self) -> str:
        return "stub:retrying"

    def plan(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        *,
        decision: Any = None,
        order_state: str | None = None,
    ) -> tuple[RoutePlan, str, ModelTrace | None]:
        self.requests.append(request)
        return (
            RoutePlan(intent=self.intent),
            "pydantic_ai",
            ModelTrace(
                requests=2,
                retries=1,
                input_tokens=120,
                output_tokens=18,
                latency_ms=25.0,
            ),
        )


def _app(tmp_path: Path, brain: Any) -> FastAPI:
    application = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+pysqlite:///{tmp_path / 'llm-usage.db'}",
            auto_create_tables=True,
            agent_mode="pydantic_ai",
        ),
        brain=brain,
    )
    seed_all(application.state.session_factory)
    return application


def _ledger(app: FastAPI) -> list[LlmUsageRecord]:
    with app.state.session_factory() as session:
        return list(
            session.scalars(
                select(LlmUsageRecord).order_by(LlmUsageRecord.created_at)
            )
        )


def test_task_route_writes_a_usage_row_with_the_retry_count(tmp_path: Path) -> None:
    brain = RetryingBrain()
    app = _app(tmp_path, brain)
    client = TestClient(app)

    response = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={"request": "帮我看看这个订单 ORD-DEMO-1001"},
    )
    assert response.status_code == 201, response.text
    task_id = response.json()["task_id"]

    with app.state.session_factory() as session:
        payload = session.scalar(
            select(TaskEvent.payload).where(
                TaskEvent.task_id == task_id,
                TaskEvent.event_type == "task.routed",
            )
        )
    assert payload is not None
    # The span/event is the observable copy; the table is the durable one.
    assert payload["model"]["model_requests"] == 2
    assert payload["model"]["model_retries"] == 1
    assert payload["model"]["model_status"] == "OK"

    rows = _ledger(app)
    assert len(rows) == 1
    row = rows[0]
    assert row.source == "task"
    assert row.task_id == task_id
    assert row.tenant_id == "demo"
    assert row.user_id == "alice"
    assert row.model_name == "stub:retrying"
    assert (row.requests, row.retries) == (2, 1)
    assert (row.input_tokens, row.output_tokens) == (120, 18)
    assert row.latency_ms == 25
    assert row.status == "OK"


def test_chat_gap_writes_its_own_row_even_when_nothing_is_adopted(
    tmp_path: Path,
) -> None:
    brain = RetryingBrain()
    app = _app(tmp_path, brain)
    client = TestClient(app)

    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={
            "message": "东西根本没法用，我不想留了，把付的钱给我回来",
            "conversation_id": "c-gap",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "UNKNOWN"
    assert body["task"] is None

    rows = _ledger(app)
    assert len(rows) == 1
    assert rows[0].source == "chat"
    assert rows[0].conversation_id == body["conversation_id"]
    assert rows[0].task_id is None
    assert rows[0].retries == 1
    # The gap was asked and answered; the row is evidence of the spend.
    assert rows[0].planning_source == "pydantic_ai"


def test_usage_summary_splits_sources_and_stays_inside_the_tenant(
    app_factory: Any,
) -> None:
    app = app_factory()
    with app.state.session_factory() as session:
        service = LlmUsageService(session)
        service.record(
            tenant_id="demo",
            user_id="alice",
            source="chat",
            planning_source="pydantic_ai",
            requests=1,
            retries=0,
            input_tokens=10,
            output_tokens=5,
            latency_ms=20.0,
            model_name="stub",
        )
        service.record(
            tenant_id="demo",
            user_id="alice",
            task_id="task_demo",
            source="task",
            planning_source="pydantic_ai",
            requests=2,
            retries=1,
            input_tokens=30,
            output_tokens=10,
            latency_ms=40.0,
            model_name="stub",
        )
        # Another tenant's spend must never enter demo's aggregate.
        service.record(
            tenant_id="acme",
            source="task",
            planning_source="pydantic_ai",
            requests=7,
            retries=0,
            latency_ms=999.0,
        )
        session.commit()

        summary = service.summary("demo", hours=24, limit=10)

    assert summary["totals"]["calls"] == 2
    assert summary["totals"]["requests"] == 3
    assert summary["totals"]["retries"] == 1
    assert summary["totals"]["input_tokens"] == 40
    assert summary["totals"]["avg_latency_ms"] == 30.0
    assert [item["source"] for item in summary["by_source"]] == ["chat", "task"]
    by_source = {item["source"]: item for item in summary["by_source"]}
    assert by_source["task"]["retries"] == 1
    assert by_source["chat"]["retries"] == 0
    assert len(summary["recent"]) == 2


def test_llm_usage_api_is_seat_only_and_tenant_scoped(app_factory: Any) -> None:
    app = app_factory()
    with app.state.session_factory() as session:
        LlmUsageService(session).record(
            tenant_id="demo",
            source="task",
            planning_source="pydantic_ai",
            requests=1,
            latency_ms=12.0,
        )
        LlmUsageService(session).record(
            tenant_id="acme",
            source="chat",
            planning_source="pydantic_ai",
            requests=9,
            latency_ms=500.0,
        )
        session.commit()
    client = TestClient(app)

    denied = client.get("/api/v1/analytics/llm-usage", headers=ALICE)
    assert denied.status_code == 403, denied.text

    allowed = client.get("/api/v1/analytics/llm-usage", headers=REVIEWER)
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    assert body["totals"]["calls"] == 1
    assert body["totals"]["requests"] == 1
    assert [item["source"] for item in body["by_source"]] == ["task"]
    assert body["recent"][0]["source"] == "task"

    # The other tenant's staff sees only its own row.
    other = client.get(
        "/api/v1/analytics/llm-usage",
        headers={**CAROL, "X-Roles": "support"},
    )
    assert other.status_code == 200, other.text
    assert other.json()["totals"]["requests"] == 9
