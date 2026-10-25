"""The model's one vote is real, and the chat path never calls it in.

Three facts the README's「模型在哪一层」section rests on — written as assertions
instead of prose:

1. the rule table reads refund and hand-off phrasings that a human has no
   trouble with as ``UNKNOWN``;
2. ``IntentBrain`` does recover them when a model fills the gap, and only there:
   a message the rules already read keeps the rule's intent even when the model
   disagrees;
3. the chat path cannot benefit from (2). ``classify_support`` hands a message to
   the write path only when the rules read it as a write intent
   (``app/agent/support_agent.py:108-111``), so an ``UNKNOWN`` turn never becomes
   a task and ``TaskRuntime._run`` never calls the brain for it. The direct
   ``POST /api/v1/tasks`` route does, because there the request text *is* the
   task.

No model server is needed: the extractor is stubbed, so these run in CI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent.pydantic_agent import IntentAgent, IntentBrain, IntentOutput
from app.agent.routing import classify
from app.core.config import Settings
from app.main import create_app
from app.models.task import Task
from app.seed import seed_all
from tests.conftest import ALICE

# A human reads a refund request in every one of these; the rule table does not.
REFUND_GAPS = (
    "ORD-DEMO-1001 那笔钱给我退回来",
    "我申请退一部分款",
    "东西根本没法用，我不想留了，把付的钱给我回来",
)
# Written from the rule vocabulary, so the rules read it first and keep it.
RULE_READABLE_REFUND = "我要申请退款，订单 ORD-DEMO-1001，金额 129 元"


class StubAgent:
    """Stand-in for ``IntentAgent``: returns a fixed extraction, counts calls."""

    def __init__(self, output: IntentOutput) -> None:
        self.output = output
        self.calls = 0

    def parse(self, request: str, context: dict[str, Any] | None = None) -> IntentOutput:
        self.calls += 1
        return self.output


class RecordingBrain:
    """Delegates to the rules and remembers that it was asked."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def plan(self, request: str, context: dict[str, Any] | None = None) -> tuple[Any, str]:
        self.requests.append(request)
        return classify(request, context), "pydantic_ai"


def _app(tmp_path: Path, brain: Any) -> FastAPI:
    application = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+pysqlite:///{tmp_path / 'gap.db'}",
            auto_create_tables=True,
            agent_mode="pydantic_ai",
        ),
        brain=brain,
    )
    seed_all(application.state.session_factory)
    return application


def test_rules_read_these_refund_phrasings_as_unknown() -> None:
    for text in REFUND_GAPS:
        assert classify(text, {}).intent.value == "UNKNOWN", text
    # ... while the task runtime is the only place that could ask a model.
    assert classify(RULE_READABLE_REFUND, {}).intent.value == "REFUND_REQUEST"


def test_brain_adopts_the_model_only_where_the_rules_left_a_gap() -> None:
    model_says = IntentOutput(
        intent="REFUND_REQUEST",
        order_id=None,
        amount_cents=None,
        confidence=0.9,
        decision_summary="用户要退钱。",
    )
    agent = StubAgent(model_says)
    brain = IntentBrain(mode="pydantic_ai", agent=cast("IntentAgent", agent))

    # Gap: rules give UNKNOWN, so the model's reading is adopted.
    recovered, source = brain.plan(REFUND_GAPS[1], {})
    assert recovered.intent.value == "REFUND_REQUEST"
    assert source == "pydantic_ai"

    # Not a gap: the rules already read it, so a disagreeing model is ignored.
    quiet = IntentOutput(
        intent="TICKET_CREATE",
        order_id=None,
        amount_cents=None,
        confidence=0.9,
        decision_summary="想转人工。",
    )
    stubborn = IntentBrain(mode="pydantic_ai", agent=cast("IntentAgent", StubAgent(quiet)))
    kept, _ = stubborn.plan(RULE_READABLE_REFUND, {"order_id": "ORD-DEMO-1001"})
    assert kept.intent.value == "REFUND_REQUEST"


def test_chat_turns_that_the_rules_cannot_read_never_reach_the_model(
    tmp_path: Path,
) -> None:
    brain = RecordingBrain()
    app = _app(tmp_path, brain)
    client = TestClient(app)

    for text in REFUND_GAPS:
        response = client.post(
            "/api/v1/support/messages",
            headers=ALICE,
            json={"message": text, "conversation_id": f"gap-{text[:6]}"},
        )
        assert response.status_code == 200, response.text

    assert brain.requests == []
    with app.state.session_factory() as session:
        assert int(session.scalar(select(func.count()).select_from(Task)) or 0) == 0


def test_the_task_api_does_ask_the_model_about_the_same_text(tmp_path: Path) -> None:
    brain = RecordingBrain()
    app = _app(tmp_path, brain)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", headers=ALICE, json={"request": REFUND_GAPS[0]})

    assert response.status_code == 201, response.text
    assert brain.requests == [REFUND_GAPS[0]]
    body = response.json()
    assert body["status"] == "SUCCEEDED"
    assert body["result"]["outcome"] == "NEEDS_HUMAN_REVIEW"
    # The rules-only plan this stub returns is UNKNOWN, which is what a chat turn
    # would have been if the chat path had ever created a task for it.
    assert "无法判断" in str(body["result"]["summary"])
