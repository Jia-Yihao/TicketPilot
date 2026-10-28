"""P2: the task ledger reuses a model reading only after verifying it.

The chat entry pays for one call when the rules cannot read a sentence. The
write path that follows used to ask again; now it receives a decision record
and checks it against its own request, rule set and model configuration. These
tests hold both halves: reuse on a match (one call, one usage row) and a
recompute on every mismatch, so the safety property "the ledger does not trust
the entry" survives the saving.

A third half arrived with the re-seal ruling (ADR-030 决策 6): when a server
derived key enters the context after the model answered, the record is resealed
over the final context and the trace carries a marker saying so. These tests
hold the marker on that turn and its absence on every turn that resealed
nothing, so the marker cannot be vacuously true.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent.decision import ModelDecision, request_digest
from app.agent.pydantic_agent import (
    IntentAgent,
    IntentBrain,
    IntentOutput,
    ModelCallError,
    ModelTrace,
)
from app.agent.routing import RULESET_FINGERPRINT, classify
from app.core.config import Settings
from app.main import create_app
from app.models.llm_usage import LlmUsageRecord
from app.models.task import Task, TaskEvent
from app.seed import seed_all
from tests.conftest import ALICE

# One sentence with every slot: order id, amount and a refund reason are all in
# the text, so the same turn that consults the model also creates the task.
COMPLETE_REFUND = "ORD-DEMO-1001 那笔钱 129 元给我退回来，质量有问题"


class CountingAgent:
    """A model with a fixed reading; counts how often it was actually asked."""

    model_name = "stub:decision"

    def __init__(self, *, intent: str = "REFUND_REQUEST", fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail
        self.output = IntentOutput(
            intent=cast("Any", intent),
            order_id=None,
            amount_cents=None,
            confidence=0.9,
            decision_summary="用户要退钱。",
        )

    def parse(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        candidates: object = None,
    ) -> tuple[IntentOutput, ModelTrace]:
        self.calls += 1
        if self.fail:
            raise ModelCallError("upstream down", trace=ModelTrace(status="FAILED"))
        return self.output, ModelTrace(requests=1, input_tokens=10, output_tokens=2)


class TwoTurnAgent(CountingAgent):
    """Fills the missing amount on the turn that mentions it, like a live model."""

    def parse(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        candidates: object = None,
    ) -> tuple[IntentOutput, ModelTrace]:
        self.calls += 1
        amount = 12900 if "129" in request else None
        output = IntentOutput(
            intent=cast("Any", "REFUND_REQUEST"),
            order_id=None,
            amount_cents=amount,
            confidence=0.9,
            decision_summary="用户要退钱。",
        )
        return output, ModelTrace(requests=1, input_tokens=10, output_tokens=2)


def _brain(agent: CountingAgent) -> IntentBrain:
    return IntentBrain(mode="pydantic_ai", agent=cast("IntentAgent", agent))


def _record(brain: IntentBrain, request: str, context: dict[str, Any]) -> ModelDecision:
    rules = classify(request, context)
    routed, _source, trace = brain.plan(request, context)
    assert trace is not None
    return ModelDecision(
        digest=request_digest(request, context),
        ruleset=RULESET_FINGERPRINT,
        model_name=brain.model_name,
        rule_intent=rules.intent.value,
        rule_order_id=rules.order_id,
        rule_amount_cents=rules.amount_cents,
        model_intent=routed.intent.value,
        model_order_id=routed.order_id,
        model_amount_cents=routed.amount_cents,
        trace=trace.as_dict(),
    )


def _app(tmp_path: Path, brain: Any, *, agent_mode: str = "pydantic_ai") -> FastAPI:
    application = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+pysqlite:///{tmp_path / 'decision.db'}",
            auto_create_tables=True,
            agent_mode=agent_mode,
        ),
        brain=brain,
    )
    seed_all(application.state.session_factory)
    return application


def _usage(app: FastAPI) -> list[LlmUsageRecord]:
    with app.state.session_factory() as session:
        return list(session.scalars(select(LlmUsageRecord)))


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
    assert row is not None
    return dict(row[0])


# ------------------------------------------------------------------ the brain
def test_a_verified_record_is_reused_without_calling_the_model() -> None:
    agent = CountingAgent()
    brain = _brain(agent)
    context = {"order_id": "ORD-DEMO-1001", "amount_cents": 12900}
    record = _record(brain, COMPLETE_REFUND, context)
    assert agent.calls == 1

    routed, source, trace = brain.plan(COMPLETE_REFUND, context, decision=record)

    assert agent.calls == 1
    assert routed.intent.value == "REFUND_REQUEST"
    assert source == "pydantic_ai"
    assert trace is not None and trace.reused is True
    assert trace.as_dict()["model_reused"] is True


def test_every_mismatch_falls_back_to_a_fresh_call() -> None:
    agent = CountingAgent()
    brain = _brain(agent)
    context: dict[str, Any] = {"order_id": "ORD-DEMO-1001", "amount_cents": 12900}
    record = _record(brain, COMPLETE_REFUND, context)

    tampered = [
        replace(record, digest="0" * 64),
        replace(record, ruleset="f" * 64),
        replace(record, model_name="someone-else"),
        replace(record, rule_intent="TICKET_CREATE"),
        replace(record, rule_amount_cents=1),
    ]
    for candidate in tampered:
        calls_before = agent.calls
        _routed_plan, _source, trace = brain.plan(
            COMPLETE_REFUND, context, decision=candidate
        )
        assert agent.calls == calls_before + 1
        assert trace is not None and trace.reused is False

    # A record for another request cannot be replayed either.
    calls_before = agent.calls
    brain.plan("我申请退一部分款", context, decision=record)
    assert agent.calls == calls_before + 1


def test_rules_mode_ignores_a_record_and_never_calls() -> None:
    agent = CountingAgent()
    record = ModelDecision(
        digest=request_digest(COMPLETE_REFUND, {}),
        ruleset=RULESET_FINGERPRINT,
        model_name="stub:decision",
        rule_intent="UNKNOWN",
        rule_order_id=None,
        rule_amount_cents=None,
        model_intent="REFUND_REQUEST",
        model_order_id=None,
        model_amount_cents=None,
        trace={},
    )
    rules_only = IntentBrain(mode="rules", agent=cast("IntentAgent", agent))

    routed, source, trace = rules_only.plan(COMPLETE_REFUND, {}, decision=record)

    assert agent.calls == 0
    assert source == "rules"
    assert trace is None
    assert routed.intent.value == "UNKNOWN"


# --------------------------------------------------------------- end to end
def test_a_complete_refund_turn_asks_once_and_writes_one_usage_row(
    tmp_path: Path,
) -> None:
    agent = CountingAgent()
    app = _app(tmp_path, _brain(agent))
    client = TestClient(app)

    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": COMPLETE_REFUND, "conversation_id": "decision-e2e"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["intent"] == "REFUND_REQUEST"
    assert payload["task"]["status"] == "WAITING_APPROVAL"
    assert agent.calls == 1
    assert _tasks(app) == 1

    # The record is an in-memory argument, not task state: the task keeps the
    # caller's words, and nothing names a decision in its context.
    with app.state.session_factory() as session:
        task_row = session.get(Task, payload["task"]["task_id"])
    assert task_row is not None
    assert task_row.request == COMPLETE_REFUND
    assert not {"decision", "model_decision"} & task_row.context.keys()

    usage = _usage(app)
    assert [(row.source, row.planning_source) for row in usage] == [
        ("chat", "pydantic_ai")
    ]

    routed = _routed(payload["task"]["task_id"], app)
    assert routed["planning_source"] == "pydantic_ai"
    assert routed["model"]["model_reused"] is True
    assert routed["model"]["model_requests"] == 1


def test_a_two_turn_clarification_pays_once_per_turn(tmp_path: Path) -> None:
    """The boundary ADR-030 §5 declares: each turn pays once, no cross-turn reuse."""

    agent = TwoTurnAgent()
    app = _app(tmp_path, _brain(agent))
    client = TestClient(app)
    first = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={
            "message": "ORD-DEMO-1001 那笔钱给我退回来，质量有问题",
            "conversation_id": "decision-two-turn",
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["task"] is None
    assert agent.calls == 1
    conversation_id = first.json()["conversation_id"]

    second = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": "129 元", "conversation_id": conversation_id},
    )
    assert second.status_code == 200, second.text
    assert second.json()["task"]["status"] == "WAITING_APPROVAL"
    # A different request digest per turn: the second turn cannot reuse the first.
    assert agent.calls == 2
    assert len(_usage(app)) == 2

    routed = _routed(second.json()["task"]["task_id"], app)
    assert routed["model"]["model_requests"] == 1


def test_a_model_failure_creates_no_task_and_no_wrong_write(tmp_path: Path) -> None:
    agent = CountingAgent(fail=True)
    app = _app(tmp_path, _brain(agent))
    client = TestClient(app)

    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": COMPLETE_REFUND, "conversation_id": "decision-fail"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["intent"] != "REFUND_REQUEST"
    assert response.json()["task"] is None
    assert _tasks(app) == 0
    # The failed call is still recorded; it bought nothing.
    assert [(row.source, row.status) for row in _usage(app)] == [("chat", "FAILED")]


def test_rules_mode_stays_at_zero_calls(tmp_path: Path) -> None:
    agent = CountingAgent()
    app = _app(tmp_path, _brain(agent), agent_mode="rules")
    client = TestClient(app)

    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": COMPLETE_REFUND, "conversation_id": "decision-rules"},
    )

    assert response.status_code == 200, response.text
    assert agent.calls == 0
    assert _usage(app) == []


def test_a_reused_reading_carries_its_reason_without_rechecking_or_recalling() -> None:
    """规格「复用命中时原样带回 reason」：复用只搬运，不重新判定、不多花一次调用。

    这里用一句规则**读得出**退款的话：模型答了个目录外的商品问题，于是那一次的
    ``model_reason`` 是 ``model_out_of_candidates``，而路由结果仍是规则读数
    （``REFUND_REQUEST``），记录因此可复用。复用它时必须把当初那句「为什么没采纳」
    原样带回来 —— 重新判定会凭空造出一个模型当时没给过的结论，再问一次则把钱花了。
    """
    rules_read_this = "我要申请退款，订单 ORD-DEMO-1001，金额 129 元"
    agent = CountingAgent(intent="PRODUCT_QUERY")
    brain = _brain(agent)

    record = _record(brain, rules_read_this, {})
    assert agent.calls == 1
    assert record.trace["model_reason"] == "model_out_of_candidates"
    assert record.model_intent == "REFUND_REQUEST"

    routed, source, trace = brain.plan(rules_read_this, {}, decision=record)

    # 一次调用都没有多花，读数与当初逐字相同。
    assert agent.calls == 1
    assert source == "pydantic_ai"
    assert routed.intent.value == "REFUND_REQUEST"
    assert trace is not None
    assert trace.reused is True
    assert trace.reason == "model_out_of_candidates"
    # 留痕面看到的也是同一个原因，而不是复用那一刻重算出来的东西。
    assert trace.as_dict()["model_reason"] == "model_out_of_candidates"
    assert trace.as_dict()["model_reused"] is True


# ------------------------------------------------------- 重封留痕（ADR-030 决策 6）
RESEAL_MARKER = "decision.reseal"
# 规则读得出退款、且原因关键词也在句子里的一句话：模型根本不会被问，因此这条
# 回合不存在「重封」这回事——痕迹必须不出现。
RULES_READ_WITH_REASON = "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，质量有问题"
# 模型被问了、也采纳了退款，但这句原话里没有任何原因关键词：迟发匹配未命中，
# 于是既没有填槽也没有重封——痕迹同样必须不出现。
MODEL_READ_WITHOUT_REASON = "ORD-DEMO-1001 那笔钱 129 元给我退回来"


def _spans(client: TestClient, trace_id: str) -> list[dict[str, Any]]:
    response = client.get(f"/api/v1/traces/{trace_id}", headers=ALICE)
    assert response.status_code == 200, response.text
    return list(response.json()["spans"])


class RecordingAgent(CountingAgent):
    """Counts calls and keeps everything the model was actually shown."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.seen: list[str] = []

    def parse(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        candidates: object = None,
    ) -> tuple[IntentOutput, ModelTrace]:
        self.seen.append(
            json.dumps(
                {
                    "request": request,
                    "context": context or {},
                    "candidates": str(candidates),
                },
                ensure_ascii=False,
                default=str,
            )
        )
        return super().parse(request, context, candidates)


def test_a_resealed_record_marks_the_trace_and_still_costs_one_call(
    tmp_path: Path,
) -> None:
    """重封必须留痕：记录是在终态上下文上重算的，不能让 trace 谎称模型见过该键。

    ``COMPLETE_REFUND`` 正是那条通路：规则把这句读成非退款（「退回来」不在意图表里），
    模型答退款并被采纳，于是本回合的退款原因是**模型调用之后**由关键词规则补进
    ``context`` 的服务端派生键。摘要因此以终态上下文重封（用户裁决：允许，但必须
    留痕），而「一次对话只问一次模型」的既有读数不得因此变成两次。
    """
    agent = RecordingAgent()
    app = _app(tmp_path, _brain(agent))
    client = TestClient(app)

    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": COMPLETE_REFUND, "conversation_id": "decision-reseal"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # 重封不改行为：一次调用、一张待审批单，台账仍然复核通过并复用。
    assert agent.calls == 1
    assert body["task"]["status"] == "WAITING_APPROVAL"
    assert _routed(body["task"]["task_id"], app)["model"]["model_reused"] is True

    markers = [span for span in _spans(client, body["trace_id"]) if span["name"] == RESEAL_MARKER]
    assert len(markers) == 1, markers
    assert markers[0]["kind"] == "understand"
    # 痕迹说的是「哪些服务端派生键进了终态上下文」，不是「模型多问了一次」。
    assert markers[0]["attributes"]["derived_keys"] == ["refund_reason"]

    # 硬性边界：痕迹不进客户可见文本、不进模型输入、不进落库的任务上下文。
    assert RESEAL_MARKER not in body["reply"]
    assert agent.seen, "the model was asked, so there is something to check"
    assert all(RESEAL_MARKER not in seen for seen in agent.seen)
    with app.state.session_factory() as session:
        task_row = session.get(Task, body["task"]["task_id"])
    assert task_row is not None
    assert RESEAL_MARKER not in json.dumps(task_row.context, ensure_ascii=False)


def test_a_late_match_that_misses_leaves_no_marker(tmp_path: Path) -> None:
    """迟发匹配未命中时没有任何后置键，因此没有重封，痕迹也不许出现。

    这一半是让上一条不断言恒真：模型照样被问了 1 次、退款照样被采纳，只是原因
    没被读出来 —— 该键不出现（口径 §5.6.2），摘要因此没有理由被重算。
    """
    agent = RecordingAgent()
    app = _app(tmp_path, _brain(agent))
    client = TestClient(app)

    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={
            "message": MODEL_READ_WITHOUT_REASON,
            "conversation_id": "decision-no-reason",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert agent.calls == 1
    assert body["intent"] == "REFUND_REQUEST"
    assert body["task"] is None  # 缺原因，本轮不建单
    assert body["slots"]["refund_reason"] is None

    markers = [span for span in _spans(client, body["trace_id"]) if span["name"] == RESEAL_MARKER]
    assert markers == []


def test_a_rule_read_refund_never_reseals(tmp_path: Path) -> None:
    """规则直读退款 + 直读原因：原因在模型之前就读好了，因此没有东西需要后置。

    这条钉的是「重封」的另一半前提：只有**模型调用之后**才补进上下文的键才会触发
    重算。规则自己把 ``refund_reason`` 读进 ``route`` 的回合，迟发匹配块整块被跳过
    （``route.refund_reason is None`` 不成立），摘要因此从未被改动，痕迹不许出现。
    这里不断言调用次数：``AGENT_MODE=pydantic_ai`` 下台账仍会自己问一次模型，那与
    本回合是否重封无关。
    """
    agent = RecordingAgent()
    app = _app(tmp_path, _brain(agent))
    client = TestClient(app)

    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={
            "message": RULES_READ_WITH_REASON,
            "conversation_id": "decision-rules-read",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # 原因由规则在分类时就读好了，所以本轮直接建单，没有被追问。
    assert body["intent"] == "REFUND_REQUEST"
    assert body["slots"]["refund_reason"] is None  # 建单即清除（口径 §5.6.1.1 生命周期）
    assert body["task"]["status"] == "WAITING_APPROVAL"

    markers = [span for span in _spans(client, body["trace_id"]) if span["name"] == RESEAL_MARKER]
    assert markers == []
