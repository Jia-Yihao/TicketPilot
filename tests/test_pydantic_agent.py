from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from app.agent.pydantic_agent import IntentAgent, IntentBrain, IntentOutput, ModelTrace
from app.models.enums import Intent


def _agent(**output: object) -> IntentAgent:
    values = {
        "intent": "UNKNOWN",
        "order_id": None,
        "amount_cents": None,
        "confidence": 0.9,
        "decision_summary": "test",
    }
    values.update(output)
    return IntentAgent(model=TestModel(custom_output_args=values))


def _scripted_agent(outputs: list[dict[str, Any]]) -> IntentAgent:
    """A model that returns each dict in turn, so retries are observable."""

    remaining = list(outputs)

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        args = remaining.pop(0) if remaining else outputs[-1]
        return ModelResponse(
            parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)]
        )

    return IntentAgent(model=FunctionModel(respond))


def test_structured_output_round_trip() -> None:
    agent = _agent(intent="ORDER_QUERY", order_id="ORD-DEMO-1001")

    output, trace = agent.parse(
        "查订单 ORD-DEMO-1001", {"order_id": "ORD-DEMO-1001"}
    )

    assert output.intent == "ORDER_QUERY"
    assert output.order_id == "ORD-DEMO-1001"
    assert trace.status == "OK"


def test_a_rejected_order_id_is_retried_once_and_counted() -> None:
    agent = _scripted_agent(
        [
            {
                "intent": "REFUND_REQUEST",
                "order_id": "DEMO-1001",
                "amount_cents": 12900,
                "confidence": 0.9,
                "decision_summary": "test",
            },
            {
                "intent": "REFUND_REQUEST",
                "order_id": "ORD-DEMO-1001",
                "amount_cents": 12900,
                "confidence": 0.9,
                "decision_summary": "test",
            },
        ]
    )

    output, trace = agent.parse("ORD-DEMO-1001 那笔钱给我退回来")

    assert output.order_id == "ORD-DEMO-1001"
    assert trace.requests == 2
    assert trace.retries == 1
    assert agent.usage.retries == 1
    assert agent.usage.model_requests == 2


def test_a_clean_run_has_zero_retries() -> None:
    agent = _agent(intent="ORDER_QUERY", order_id="ORD-DEMO-1001")

    _output, trace = agent.parse("查订单 ORD-DEMO-1001")

    assert trace.requests == 1
    assert trace.retries == 0


def test_deterministic_rules_keep_priority() -> None:
    brain = IntentBrain(
        mode="pydantic_ai",
        agent=_agent(intent="POLICY_QA", amount_cents=999),
    )

    plan, source, _trace = brain.plan("我要申请退款，订单 ORD-DEMO-1001，金额 129 元")

    assert source == "pydantic_ai"
    assert plan.intent is Intent.REFUND_REQUEST
    assert plan.order_id == "ORD-DEMO-1001"
    assert plan.amount_cents == 12900


def test_the_model_still_fills_an_order_id_but_not_a_read_intent() -> None:
    """R7 采纳面：模型可以把规则读不出的订单号补上，但读意图改不动路由。

    收口之前六个意图都在采纳面内，``ORDER_QUERY`` 也能改道；现在只有
    ``ADOPTABLE_INTENTS``（退款）能改，订单号补位这条既有通路不受影响
    （``.qoder/rules/adoption-surface.md`` §2.2）。
    """
    brain = IntentBrain(
        mode="pydantic_ai",
        agent=_agent(intent="ORDER_QUERY", order_id="ORD-DEMO-1001"),
    )

    plan, source, _trace = brain.plan("麻烦看一下 ORD-DEMO-1001")

    assert source == "pydantic_ai"
    assert plan.intent is Intent.UNKNOWN
    assert plan.order_id == "ORD-DEMO-1001"


def test_rules_mode_does_not_call_the_model() -> None:
    brain = IntentBrain(mode="rules", agent=_agent(intent="ORDER_QUERY"))

    plan, source, _trace = brain.plan("今天天气怎么样？")

    assert source == "rules"
    assert plan.intent is Intent.UNKNOWN


def test_pydantic_ai_mode_calls_the_model_without_short_circuit() -> None:
    calls: list[str] = []

    class CountingAgent:
        def parse(
            self,
            request: str,
            context: dict | None = None,
            candidates: object = None,
        ) -> tuple[IntentOutput, object]:
            calls.append(request)
            return IntentOutput(
                intent="UNKNOWN", order_id=None, amount_cents=None,
                confidence=0.1, decision_summary="stub",
            ), None

    brain = IntentBrain(mode="pydantic_ai", agent=CountingAgent())  # type: ignore[arg-type]
    request = "我要申请退款，订单 ORD-DEMO-1001，金额 129 元"

    plan, source, trace = brain.plan(request)

    assert calls == [request]
    assert source == "pydantic_ai"
    assert plan.intent is Intent.REFUND_REQUEST
    assert plan.amount_cents == 12900
    assert trace is None


def test_model_failure_falls_back_to_rules() -> None:
    class BrokenAgent:
        def parse(
            self,
            request: str,
            context: dict | None = None,
            candidates: object = None,
        ) -> None:
            raise RuntimeError("provider unavailable")

    brain = IntentBrain(mode="pydantic_ai", agent=BrokenAgent())  # type: ignore[arg-type]

    plan, source, trace = brain.plan("订单 ORD-DEMO-1001 的物流到哪了？")

    assert source == "rules_fallback"
    assert plan.intent is Intent.LOGISTICS_QUERY
    assert plan.order_id == "ORD-DEMO-1001"
    assert trace is not None
    assert trace.failed


def test_a_trace_without_a_decision_keeps_the_pre_r7_key_set() -> None:
    """`.qoder/rules/adoption-surface.md` §4 DoD 5：没做过判定的 trace 不长出四个新键。

    键集要与 R7 之前逐字一致（四个字段全为 `None` 时不落盘），而反解一个空字典
    得到的是四个 `None`（「没有记录过判定」），不是四个 `False`（「判过且否」）。
    """

    assert set(ModelTrace(requests=1).as_dict().keys()) == {
        "model_requests",
        "model_retries",
        "model_input_tokens",
        "model_output_tokens",
        "model_latency_ms",
        "model_status",
        "model_instrumented",
        "model_reused",
        "model_reason",
        "model_candidates",
    }

    restored = ModelTrace.from_dict({})
    assert restored.adopted is None
    assert restored.redirect is None
    assert restored.redirect_rejected is None
    assert restored.redirect_kind is None
