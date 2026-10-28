"""P8 模型面：模型只能说候选里的意图。

全部离线：`TestModel` / `FunctionModel` 是 pydantic-ai 自带的假模型，不发网络请求。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from app.agent.candidates import CandidateSet
from app.agent.intent_catalog import get_catalog
from app.agent.pydantic_agent import (
    IntentAgent,
    IntentBrain,
    IntentOutput,
    ModelTrace,
    decide_adoption,
)
from app.agent.routing import classify
from app.models.enums import Intent

CATALOG = get_catalog()

# A message whose only content evidence is logistics: PRODUCT_QUERY is not a
# candidate here, so a model that names it is out of the set.
LOGISTICS = "ORD-DEMO-1001 的物流到哪了"
# A message the rules cannot read at all; the conversation context names an
# intent, so that row is a candidate with nothing but context evidence.
GAP = "这个怎么处理一下"


def _agent(**output: object) -> IntentAgent:
    values: dict[str, object] = {
        "intent": "UNKNOWN",
        "order_id": None,
        "amount_cents": None,
        "confidence": 0.9,
        "decision_summary": "test",
    }
    values.update(output)
    return IntentAgent(model=TestModel(custom_output_args=values))


def _brain(**output: object) -> IntentBrain:
    return IntentBrain(mode="pydantic_ai", agent=_agent(**output))  # type: ignore[arg-type]


def _prompt_text(messages: list[ModelMessage]) -> str:
    chunks: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            content = getattr(part, "content", None)
            if isinstance(content, str):
                chunks.append(content)
    return "\n".join(chunks)


class RecordingAgent:
    """A stub that keeps whatever the brain hands it, so the caller is observable."""

    def __init__(self, **output: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self._output = IntentOutput(
            intent=output.get("intent", "UNKNOWN"),
            order_id=output.get("order_id"),
            amount_cents=output.get("amount_cents"),
            confidence=output.get("confidence", 0.5),
            decision_summary="stub",
        )
        self.model_name = "RecordingAgent"

    def parse(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        candidates: CandidateSet | None = None,
    ) -> tuple[IntentOutput, ModelTrace]:
        self.calls.append({"request": request, "context": context, "candidates": candidates})
        return self._output, ModelTrace(requests=1)


def test_an_in_candidate_refund_reading_is_used_exactly_as_before() -> None:
    """采纳面（`ADOPTABLE_INTENTS`）里唯一的一行：退款读数照旧被采用。"""
    brain = IntentBrain(
        mode="pydantic_ai",
        agent=_agent(intent="REFUND_REQUEST", order_id="ORD-DEMO-1001"),  # type: ignore[arg-type]
    )

    plan, source, trace = brain.plan(GAP, {"last_intent": "REFUND_REQUEST"})

    assert source == "pydantic_ai"
    assert plan.intent is Intent.REFUND_REQUEST
    assert plan.order_id == "ORD-DEMO-1001"
    assert trace is not None
    assert trace.reason is None
    assert trace.adopted is True
    assert trace.redirect is True
    assert trace.redirect_rejected is False
    assert trace.redirect_kind == "extra_write"
    assert trace.as_dict()["model_redirect"] is True

    # 另外三个改道方向由纯函数 `redirect_kind` 逐条钉住（不新增用例，收集数不动）。
    assert decide_adoption(Intent.REFUND_REQUEST, "ORDER_QUERY").kind == "missed_write"
    assert decide_adoption(Intent.REFUND_REQUEST, "TICKET_CREATE").kind == "write_to_write"
    assert decide_adoption(Intent.LOGISTICS_QUERY, "ORDER_QUERY").kind == "read_to_read"


def test_a_candidate_the_adoption_surface_does_not_cover_is_still_not_adopted() -> None:
    """候选化不改「服务端据此做什么」：REFUND_STATUS 在候选里，也不改写入路径的读数。"""
    brain = _brain(intent="REFUND_STATUS")

    plan, _source, trace = brain.plan(GAP, {"last_intent": "REFUND_STATUS"})

    assert plan.intent is Intent.UNKNOWN
    assert trace is not None
    assert trace.reason is None


def test_an_out_of_candidate_answer_falls_back_without_a_second_request() -> None:
    brain = _brain(intent="PRODUCT_QUERY")

    plan, source, trace = brain.plan(LOGISTICS)

    rule_plan = classify(LOGISTICS)
    assert source == "pydantic_ai"
    assert plan.intent is rule_plan.intent
    assert plan.order_id == rule_plan.order_id
    assert trace is not None
    assert trace.requests == 1
    assert trace.reason == "model_out_of_candidates"


def test_none_is_an_answer_not_a_failure() -> None:
    brain = _brain(intent="NONE")

    plan, _source, trace = brain.plan(LOGISTICS)

    assert plan.intent is Intent.LOGISTICS_QUERY
    assert trace is not None
    assert trace.requests == 1
    assert trace.reason == "model_none"


def test_the_server_bucket_never_reads_the_models_own_number() -> None:
    agent = RecordingAgent(intent="UNKNOWN", confidence=0.99)
    brain = IntentBrain(mode="pydantic_ai", agent=agent)  # type: ignore[arg-type]

    brain.plan(GAP, {"last_intent": "REFUND_STATUS"})

    handed = agent.calls[0]["candidates"]
    assert isinstance(handed, CandidateSet)
    assert handed.allows("REFUND_STATUS") is True
    assert handed.bucket_of("REFUND_STATUS") == "low"


def test_the_prompt_carries_the_candidates_and_nothing_else_from_the_catalog() -> None:
    seen: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(_prompt_text(messages))
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=info.output_tools[0].name,
                    args={
                        "intent": "LOGISTICS_QUERY",
                        "order_id": "ORD-DEMO-1001",
                        "amount_cents": None,
                        "confidence": 0.9,
                        "decision_summary": "test",
                    },
                )
            ]
        )

    brain = IntentBrain(
        mode="pydantic_ai", agent=IntentAgent(model=FunctionModel(respond))  # type: ignore[arg-type]
    )

    _plan, _source, trace = brain.plan(LOGISTICS)

    assert trace is not None
    assert trace.reason is None
    prompt = seen[0]
    assert "LOGISTICS_QUERY" in prompt
    assert "rule:LOGISTICS_QUERY" in prompt
    # 未入选的行既不进 prompt，L3 完整目录更不进。
    assert "PRODUCT_QUERY" not in prompt
    assert "REFUND_ELIGIBILITY" not in prompt
    payload = next(
        json.loads(line) for line in prompt.splitlines() if line.strip().startswith("{")
    )
    assert [item["intent_id"] for item in payload["candidates"]] == [
        "HUMAN_HANDOFF",
        "LOGISTICS_QUERY",
        "UNKNOWN",
    ]


def test_rules_mode_never_builds_candidates_and_never_calls_the_model() -> None:
    agent = RecordingAgent()
    brain = IntentBrain(mode="rules", agent=agent)  # type: ignore[arg-type]

    plan, source, trace = brain.plan(LOGISTICS)

    assert agent.calls == []
    assert source == "rules"
    assert trace is None
    assert plan.intent is Intent.LOGISTICS_QUERY


def test_the_switch_off_offers_no_candidates_and_still_refuses_a_ticket_reading() -> None:
    """关掉开关 = 不注入候选、不产生 model_reason；读数仍要过采纳面这一关。

    候选化轮之前，``TICKET_CREATE`` 也能改道（六个字面量都在采纳面里），于是任务
    API 上模型可以把一条规则读不懂的只读请求变成建单。R7 收口后采纳面只有退款，
    ``TICKET_CREATE`` 是一个**合法但被拒**的读数：路由留在规则读数上，轨迹按
    ``extra_write`` 记一次「被拒改道」，与「被采纳的改道」分列。
    """
    agent = RecordingAgent(intent="TICKET_CREATE")
    brain = IntentBrain(
        mode="pydantic_ai",
        agent=agent,  # type: ignore[arg-type]
        candidates_enabled=False,
    )

    plan, source, trace = brain.plan(GAP)

    assert agent.calls[0]["candidates"] is None
    assert source == "pydantic_ai"
    assert plan.intent is Intent.UNKNOWN
    assert trace is not None
    assert trace.reason is None
    assert trace.adopted is False
    assert trace.redirect is False
    assert trace.redirect_rejected is True
    assert trace.redirect_kind == "extra_write"
    assert trace.as_dict()["model_redirect_rejected"] is True
    assert trace.as_dict()["model_redirect"] is False


def test_the_switch_off_still_refuses_an_intent_outside_the_old_literals() -> None:
    """输出类型放宽到 str 之后，字面量之外的读数在开关关闭时也进不了路由。

    本轮唯一承认的差异：旧行为里这句话会让 pydantic 输出校验失败，落成
    ``rules_fallback``；现在模型确实答了，只是服务端不采纳，路由结果同为规则读数。
    """
    agent = RecordingAgent(intent="PRODUCT_QUERY")
    brain = IntentBrain(
        mode="pydantic_ai",
        agent=agent,  # type: ignore[arg-type]
        candidates_enabled=False,
    )

    plan, source, trace = brain.plan(LOGISTICS)

    rule_plan = classify(LOGISTICS)
    assert agent.calls[0]["candidates"] is None
    assert plan.intent is rule_plan.intent
    assert source == "pydantic_ai"
    assert trace is not None
    assert trace.reason is None
