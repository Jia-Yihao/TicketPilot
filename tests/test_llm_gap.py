"""The model's one vote on the chat path: where it is asked, what it may buy.

The README's「模型边界」section rests on these facts, written as assertions
instead of prose:

1. the rule table reads refund phrasings that a human has no trouble with as
   ``UNKNOWN``;
2. ``IntentBrain`` does recover them when a model fills the gap, and only there:
   a message the rules already read keeps the rule's intent even when the model
   disagrees;
3. the chat path now asks the model *only* on an ``UNKNOWN`` turn, and adopts
   *only* a refund reading. The refund it opens still waits for human approval,
   and ``task.routed`` records ``planning_source=pydantic_ai`` because the task
   runtime plans the request itself — the chat gate cannot hand it a verdict;
4. with ``AGENT_MODE=rules`` nothing changes: the model is never called.

No model server is needed: the extractor/brain is stubbed, so these run in CI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent.pydantic_agent import (
    IntentAgent,
    IntentBrain,
    IntentOutput,
    ModelTrace,
)
from app.agent.routing import RoutePlan, classify
from app.agent.support_agent import SupportIntent, SupportPlan, model_gate
from app.core.config import Settings
from app.main import create_app
from app.models.enums import Intent
from app.models.llm_usage import LlmUsageRecord
from app.models.task import Task, TaskEvent
from app.models.ticket import Ticket
from app.seed import seed_all
from scripts.llm_gap_probe import CANDIDATES
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

    def parse(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        candidates: object = None,
    ) -> tuple[IntentOutput, ModelTrace]:
        self.calls += 1
        return self.output, ModelTrace(requests=1)


class ExplodingAgent:
    """Fails the test if the rules-mode path ever reaches a model."""

    def parse(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        candidates: object = None,
    ) -> IntentOutput:
        raise AssertionError(f"rules mode asked the model about {request!r}")


class FixedBrain:
    """Rules first; a fixed model reading wherever the rules give ``UNKNOWN``."""

    def __init__(
        self,
        intent: str = "UNKNOWN",
        *,
        order_id: str | None = None,
        amount_cents: int | None = None,
    ) -> None:
        self.intent = intent
        self.model_order_id = order_id
        self.model_amount_cents = amount_cents
        self.requests: list[str] = []

    def plan(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        *,
        decision: Any = None,
        order_state: str | None = None,
    ) -> tuple[RoutePlan, str, ModelTrace | None]:
        self.requests.append(request)
        rules = classify(request, context)
        if rules.intent is not Intent.UNKNOWN:
            return rules, "rules", None
        if self.intent == "UNKNOWN":
            return rules, "pydantic_ai", ModelTrace(requests=1)
        return (
            RoutePlan(
                intent=Intent(self.intent),
                order_id=rules.order_id or self.model_order_id,
                amount_cents=(
                    rules.amount_cents
                    if rules.amount_cents is not None
                    else self.model_amount_cents
                ),
                # 与真实 IntentBrain 的采纳路径同形（pydantic_agent.py:428/487）：
                # 退款原因只搬运服务端读数，模型不抽取、不采纳（B-1 口径 §5.6.5）。
                refund_reason=rules.refund_reason,
            ),
            "pydantic_ai",
            ModelTrace(requests=1),
        )


def _app(tmp_path: Path, brain: Any, *, agent_mode: str = "pydantic_ai") -> FastAPI:
    application = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+pysqlite:///{tmp_path / 'gap.db'}",
            auto_create_tables=True,
            agent_mode=agent_mode,
        ),
        brain=brain,
    )
    seed_all(application.state.session_factory)
    return application


def _tasks(app: FastAPI) -> int:
    with app.state.session_factory() as session:
        return int(session.scalar(select(func.count()).select_from(Task)) or 0)


def _routed(task_id: str, app: FastAPI) -> dict[str, Any]:
    with app.state.session_factory() as session:
        row = session.execute(
            select(TaskEvent.payload)
            .where(TaskEvent.task_id == task_id, TaskEvent.event_type == "task.routed")
            .order_by(TaskEvent.created_at.desc())
        ).first()
    assert row is not None, f"no task.routed event for {task_id}"
    return dict(row[0])


def _route_attributes(client: TestClient, trace_id: str) -> dict[str, Any]:
    """The task-side routing span: ``task.route`` flattens ``ModelTrace`` onto itself."""
    response = client.get(f"/api/v1/traces/{trace_id}", headers=ALICE)
    assert response.status_code == 200, response.text
    spans = [span for span in response.json()["spans"] if span["name"] == "task.route"]
    assert len(spans) == 1, spans
    return dict(spans[0]["attributes"])


def _tickets(app: FastAPI) -> int:
    with app.state.session_factory() as session:
        return int(session.scalar(select(func.count()).select_from(Ticket)) or 0)


def test_rules_read_these_refund_phrasings_as_unknown() -> None:
    for text in REFUND_GAPS:
        assert classify(text, {}).intent.value == "UNKNOWN", text
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
    recovered, source, _trace = brain.plan(REFUND_GAPS[1], {})
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
    kept, _source, _trace = stubborn.plan(
        RULE_READABLE_REFUND, {"order_id": "ORD-DEMO-1001"}
    )
    assert kept.intent.value == "REFUND_REQUEST"


def test_rules_mode_chat_never_asks_a_model(tmp_path: Path) -> None:
    brain = IntentBrain(mode="rules", agent=cast("IntentAgent", ExplodingAgent()))
    app = _app(tmp_path, brain, agent_mode="rules")
    client = TestClient(app)

    # The first sentence carries an order id, so the catalog answers with the
    # order card it is allowed to give; the other two stay UNKNOWN.
    expected = ("ORDER_QUERY", "UNKNOWN", "UNKNOWN")
    for index, (text, intent) in enumerate(zip(REFUND_GAPS, expected, strict=True)):
        response = client.post(
            "/api/v1/support/messages",
            headers=ALICE,
            json={"message": text, "conversation_id": f"rules-{index}"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["intent"] == intent

    assert _tasks(app) == 0


def test_model_gate_names_the_three_levels() -> None:
    hit = classify(RULE_READABLE_REFUND, {})
    assert (
        model_gate(
            SupportPlan(SupportIntent.REFUND_REQUEST, "ORD-DEMO-1001"),
            hit,
            RULE_READABLE_REFUND,
        )
        == "rules_hit"
    )

    question = "运费谁出？"
    assert (
        model_gate(SupportPlan(SupportIntent.UNKNOWN), classify(question, {}), question)
        == "question"
    )

    gap = REFUND_GAPS[0]
    assert (
        model_gate(SupportPlan(SupportIntent.UNKNOWN), classify(gap, {}), gap)
        == "consult"
    )


def test_questions_never_reach_the_model_and_refunds_still_do(tmp_path: Path) -> None:
    """The gate removes calls that could buy nothing, not refund recall.

    The two information questions from the eleven labelled gaps would have
    been read as ``POLICY_QA`` / ``LOGISTICS_QUERY`` by the model — readings
    the chat path never adopts. The six refund phrasings must still be asked.
    """
    questions = [
        case.text
        for case in CANDIDATES
        if case.intent in {"POLICY_QA", "LOGISTICS_QUERY"}
    ]
    refunds = [
        case.text
        for case in CANDIDATES
        if case.intent == "REFUND_REQUEST"
        and classify(case.text, {}).intent is Intent.UNKNOWN
    ]
    assert len(questions) == 2
    # Five of the six refund phrasings are still rule gaps; the sixth contains
    # the rule vocabulary, so it is level 1 (rules hit) and is not asked either.
    assert len(refunds) == 5

    brain = FixedBrain("REFUND_REQUEST")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    for index, text in enumerate(questions):
        response = client.post(
            "/api/v1/support/messages",
            headers=ALICE,
            json={"message": text, "conversation_id": f"question-{index}"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["intent"] != "REFUND_REQUEST"
    assert brain.requests == []

    for index, text in enumerate(refunds):
        response = client.post(
            "/api/v1/support/messages",
            headers=ALICE,
            json={"message": text, "conversation_id": f"refund-{index}"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["intent"] == "REFUND_REQUEST"
    assert brain.requests == refunds

    with app.state.session_factory() as session:
        rows = list(
            session.scalars(
                select(LlmUsageRecord).where(LlmUsageRecord.source == "chat")
            )
        )
    assert len(rows) == len(refunds)


def test_chat_asks_the_model_where_the_rules_cannot_read(tmp_path: Path) -> None:
    brain = FixedBrain("UNKNOWN")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": REFUND_GAPS[0], "conversation_id": "asked"},
    )

    assert response.status_code == 200, response.text
    # Asked once, and an unhelpful answer changes nothing: the order-id
    # fallback still stands and no task is created.
    assert brain.requests == [REFUND_GAPS[0]]
    assert response.json()["intent"] == "ORDER_QUERY"
    assert _tasks(app) == 0


def test_chat_does_not_spend_a_model_call_on_a_question(tmp_path: Path) -> None:
    """``blocks_write_action`` still runs first: a question is not a request.

    The model reads "运费谁出？" as POLICY_QA, but no reading would be adopted
    for a read question, so the gate does not ask and the turn stays UNKNOWN.
    """
    brain = FixedBrain("POLICY_QA")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": "运费谁出？", "conversation_id": "shipping"},
    )

    assert response.status_code == 200, response.text
    assert brain.requests == []
    assert response.json()["intent"] == "UNKNOWN"
    assert _tasks(app) == 0


def test_chat_adopts_a_refund_reading_and_the_task_still_waits_for_approval(
    tmp_path: Path,
) -> None:
    brain = FixedBrain("REFUND_REQUEST", amount_cents=12900)
    app = _app(tmp_path, brain)
    client = TestClient(app)
    conversation = "refund-gap"
    # The gap phrasing plus the refund reason B-1 requires: still unreadable
    # by the rule table ("质量" is not an intent cue), but the runtime no
    # longer stops at MISSING_REFUND_REASON once the amount arrives.
    text = REFUND_GAPS[0] + "，质量有问题"

    first = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": text, "conversation_id": conversation},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["intent"] == "REFUND_REQUEST"
    # The intent was rescued; the order came from the text, the amount did not
    # exist anywhere yet, so the catalog still asks for it.
    assert body["clarification"]["expected_slot"] == "amount_cents"
    assert body["task"] is None
    assert _tasks(app) == 0

    second = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": "129 元", "conversation_id": body["conversation_id"]},
    )
    assert second.status_code == 200, second.text
    payload = second.json()
    assert payload["task"]["status"] == "WAITING_APPROVAL"
    assert payload["task"]["pending_approval"]["amount_cents"] == 12900

    routed = _routed(payload["task"]["task_id"], app)
    assert routed["intent"] == "REFUND_REQUEST"
    # The ledger's verdict is the runtime's own plan (it asked the model again
    # about the merged request); the chat gate cannot pass a verdict down.
    assert routed["planning_source"] == "pydantic_ai"
    assert brain.requests == [text, text]
    assert _tasks(app) == 1


def test_the_task_api_does_ask_the_model_about_the_same_text(tmp_path: Path) -> None:
    brain = FixedBrain("UNKNOWN")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", headers=ALICE, json={"request": REFUND_GAPS[0]})

    assert response.status_code == 201, response.text
    assert brain.requests == [REFUND_GAPS[0]]
    body = response.json()
    assert body["status"] == "SUCCEEDED"
    assert body["result"]["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert "无法判断" in str(body["result"]["summary"])


def test_the_task_api_cannot_reroute_a_read_request_into_a_write(tmp_path: Path) -> None:
    """R7 收口：任务 API 与对话入口共用同一个采纳面，模型改不出建单。

    直接调 ``POST /tasks`` 时没有对话入口那道闸，改道只能由 ``IntentBrain.plan``
    自己的采纳面挡住。这句话规则读成 ``UNKNOWN``，模型答 ``TICKET_CREATE``
    （合法读数、也在候选集里）：收口之前六个意图都在采纳面内，于是模型能把一条
    只读请求变成建单，而 README 的「0/16」测的是对话入口、不覆盖这条路径。
    现在路由留在规则读数上，工单表一行都不多，轨迹按 ``extra_write`` 记一次
    「被拒改道」，与「被采纳的改道」分列。
    """
    model_says = IntentOutput(
        intent="TICKET_CREATE",
        order_id=None,
        amount_cents=None,
        confidence=0.9,
        decision_summary="用户想建单。",
    )
    agent = StubAgent(model_says)
    brain = IntentBrain(mode="pydantic_ai", agent=cast("IntentAgent", agent))
    app = _app(tmp_path, brain)
    client = TestClient(app)

    response = client.post("/api/v1/tasks", headers=ALICE, json={"request": REFUND_GAPS[1]})

    assert response.status_code == 201, response.text
    assert agent.calls == 1
    body = response.json()
    # 没有建单：任务落在「无法判断」的追问型终态，工单表仍是空的。
    assert body["status"] == "SUCCEEDED"
    assert body["result"]["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert "无法判断" in str(body["result"]["summary"])
    assert _tasks(app) == 1
    assert _tickets(app) == 0

    attributes = _route_attributes(client, body["trace_id"])
    # 台账记的是路由结果（规则读数），模型那句写意图由改道计数承接。
    assert attributes["intent"] == "UNKNOWN"
    assert attributes["planning_source"] == "pydantic_ai"
    assert attributes["model_redirect_rejected"] is True
    assert attributes["model_redirect"] is False
    assert attributes["model_redirect_kind"] == "extra_write"
