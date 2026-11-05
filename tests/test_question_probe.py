"""T3 问句门只读问询通路：``IntentBrain.probe_question``。

全部离线：与 ``tests/test_intent_candidate_model.py`` 同款驱动——pydantic-ai 自带的
``TestModel`` 假模型加本地 stub agent，不发网络请求、不读数据库。

本文件只钉只读契约（`app/agent/counter_question.yaml`/§2.3/§3.2/§9）：
不产生采纳决定、trace 不长出四个计数键、rules 模式零模型调用零候选计算、失败不静默。
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import Any

import pytest
from pydantic_ai.models.test import TestModel

from app.agent.candidates import CandidateSet, build_candidates
from app.agent.intent_catalog import get_catalog
from app.agent.pydantic_agent import (
    QUESTION_PROBE_REASONS,
    IntentAgent,
    IntentBrain,
    IntentOutput,
    ModelCallError,
    ModelTrace,
    QuestionProbe,
)
from app.agent.routing import classify

# 与 test_intent_candidate_model.py 同义的消息（跨文件不导入私有常量）。
# 规则读数 LOGISTICS_QUERY；PRODUCT_QUERY 不在其候选集里。
LOGISTICS = "ORD-DEMO-1001 的物流到哪了"
# 规则读不出的问句（UNKNOWN）。
GAP = "这个怎么处理一下"

# ``ModelTrace.as_dict`` 在 R7 之前就有、且本通路不得扩充的键集合。
BASE_TRACE_KEYS = {
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
# 口径 §2.2：这四个键属于采纳面账本，问询通路一条都不许出现——不是值为 None，
# 而是键本身不在 dict 里（``as_dict`` 对 None 值省略键，所以等价，但断言键集更硬）。
COUNT_KEYS = {
    "model_adopted",
    "model_redirect",
    "model_redirect_rejected",
    "model_redirect_kind",
}


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


class RecordingAgent:
    """本地 stub：记录 brain 递给它的一切，让"未调用"和"透传"都可被观察。"""

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


class FailingAgent:
    """本地 stub：按指定异常失败，验证失败路径不静默。"""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.model_name = "FailingAgent"

    def parse(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        candidates: CandidateSet | None = None,
    ) -> tuple[IntentOutput, ModelTrace]:
        raise self._exc


# 1. rules 模式：零模型调用、零候选计算，早退形态与 ``plan`` 一致（口径 §9）。


def test_rules_mode_computes_no_candidates_and_never_calls_the_model() -> None:
    agent = RecordingAgent(intent="TICKET_CREATE")
    brain = IntentBrain(mode="rules", agent=agent)  # type: ignore[arg-type]

    probe = brain.probe_question(GAP)

    assert agent.calls == []
    assert isinstance(probe, QuestionProbe)
    assert probe.intent is None
    assert probe.candidate_set is None
    assert probe.trace is None
    assert probe.source == "rules"
    assert probe.reason == "rules_mode"
    assert probe.asks is False


def test_rules_mode_with_no_agent_at_all_probes_the_same_way() -> None:
    probe = IntentBrain(mode="rules").probe_question(GAP)

    assert probe.intent is None
    assert probe.trace is None
    assert probe.reason == "rules_mode"


# 2. 合法改道读数：原样返回、绝不采纳；trace 键集合恰好等于既有集合。


def test_a_legal_new_reading_comes_back_unadopted() -> None:
    probe = _brain(intent="TICKET_CREATE").probe_question(GAP)

    assert probe.intent == "TICKET_CREATE"
    assert probe.asks is True
    assert probe.source == "pydantic_ai"
    assert probe.reason is None
    assert probe.trace is not None
    assert set(probe.trace.as_dict()) == BASE_TRACE_KEYS
    assert not set(probe.trace.as_dict()) & COUNT_KEYS


def test_a_refund_reading_is_returned_as_is_and_never_recorded_as_adopted() -> None:
    """REFUND_REQUEST 在采纳面上；问询通路只把它当"一个新的、规则没读出的读数"。

    一旦这里调用 ``decide_adoption`` 或落 ``ModelDecision``，``plan`` 的台账复用分支
    就可能拿这条读数改道——等于事实上的问句门采纳（口径 §2.2 硬要求 1）。
    """
    probe = _brain(intent="REFUND_REQUEST").probe_question(GAP)

    assert probe.intent == "REFUND_REQUEST"
    assert probe.asks is True
    assert probe.source == "pydantic_ai"
    assert probe.reason is None
    assert probe.trace is not None
    assert not set(probe.trace.as_dict()) & COUNT_KEYS
    # 字段级也要钉住：None 是"没记录决定"，不是"决定不采纳"（R7 §2.3）。
    assert probe.trace.adopted is None
    assert probe.trace.redirect is None
    assert probe.trace.redirect_rejected is None
    assert probe.trace.redirect_kind is None


# 3. §3.2 的拒绝/无效分支：每个都有可观察的 reason，读数不进 intents 列表。


def test_none_is_an_answer_not_a_failure_for_the_probe_too() -> None:
    probe = _brain(intent="NONE").probe_question(GAP)

    assert probe.intent is None
    assert probe.asks is False
    assert probe.reason == "model_none"
    assert probe.source == "pydantic_ai"
    assert probe.trace is not None


def test_an_out_of_candidate_answer_is_refused_without_a_second_request() -> None:
    probe = _brain(intent="PRODUCT_QUERY").probe_question(LOGISTICS)

    assert probe.intent is None
    assert probe.reason == "model_out_of_candidates"
    assert probe.trace is not None
    # 越界不重试（P8 §3.2）：只有一次模型请求。
    assert probe.trace.requests == 1


def test_a_reading_that_matches_the_rules_adds_nothing_and_asks_nothing() -> None:
    """含 UNKNOWN↔UNKNOWN：模型说了规则也说的，等于没给新信息。"""
    probe = _brain(intent="UNKNOWN").probe_question(GAP)

    assert probe.intent is None
    assert probe.asks is False
    assert probe.reason == "model_unknown"
    assert probe.trace is not None
    assert probe.source == "pydantic_ai"


def test_an_unknown_reading_is_refused_even_when_the_rules_read_something_else() -> None:
    """阻塞项：UNKNOWN 是无条件候选，``allows()`` 恒真——拒绝必须落在方法自身。

    规则读数 LOGISTICS_QUERY（不是 UNKNOWN），模型答 UNKNOWN：与规则比较碰巧
    不成立也不影响，§3.1 条件 4 要求这条读数独立地被拒绝，且 trace 不丢。
    """
    probe = _brain(intent="UNKNOWN").probe_question(LOGISTICS)

    assert probe.intent is None
    assert probe.asks is False
    assert probe.reason == "model_unknown"
    assert probe.trace is not None


def test_a_disabled_candidate_path_cannot_validate_the_reading() -> None:
    brain = IntentBrain(
        mode="pydantic_ai",
        agent=_agent(intent="TICKET_CREATE"),  # type: ignore[arg-type]
        candidates_enabled=False,
    )

    probe = brain.probe_question(GAP)

    assert probe.intent is None
    assert probe.candidate_set is None
    assert probe.reason == "no_candidates"
    assert probe.source == "pydantic_ai"
    assert probe.trace is not None


def test_a_reading_outside_the_catalog_is_refused_as_out_of_candidates() -> None:
    """越界的门槛是目录/候选集：只有不在候选集里的读数才被判越界，``Intent``
    枚举之外的合法目录行不是越界。"""
    probe = _brain(intent="NOT_AN_INTENT_ROW").probe_question(GAP)

    assert probe.intent is None
    assert probe.reason == "model_out_of_candidates"
    assert probe.trace is not None
    assert probe.trace.requests == 1  # 同样不重试


def test_a_legal_catalog_row_outside_the_intent_enum_is_a_legal_reading() -> None:
    """``REFUND_STATUS`` 是合法目录行（``app/agent/intents.yaml``）但不是 ``Intent``
    枚举成员：按口径 §3.1 它是合法读数，探针原样返回，不做任何枚举转换——
    是否转成 ``Intent`` 是 T4 消费端的决定。"""
    probe = _brain(intent="REFUND_STATUS").probe_question(GAP)

    assert probe.intent == "REFUND_STATUS"
    assert probe.asks is True
    assert probe.reason is None
    assert not set(probe.trace.as_dict()) & COUNT_KEYS


def test_none_is_still_an_answer_when_the_candidate_path_is_off() -> None:
    """NONE 不需要候选集来验证，所以它排在 ``no_candidates`` 之前。"""
    brain = IntentBrain(
        mode="pydantic_ai",
        agent=_agent(intent="NONE"),  # type: ignore[arg-type]
        candidates_enabled=False,
    )

    probe = brain.probe_question(GAP)

    assert probe.intent is None
    assert probe.candidate_set is None
    assert probe.reason == "model_none"


# 4. 失败不静默：trace 必须跟着回来，source 落在 rules_fallback。


def test_a_model_call_error_carries_its_trace_into_the_probe() -> None:
    cost = ModelTrace(requests=1, status="FAILED", latency_ms=12.5)
    agent = FailingAgent(ModelCallError("boom", trace=cost))
    brain = IntentBrain(mode="pydantic_ai", agent=agent)  # type: ignore[arg-type]

    probe = brain.probe_question(GAP)

    assert probe.intent is None
    assert probe.reason == "model_failed"
    assert probe.source == "rules_fallback"
    assert probe.trace is cost
    assert probe.trace.failed is True


def test_a_generic_failure_still_produces_a_failed_trace() -> None:
    agent = FailingAgent(RuntimeError("unexpected"))
    brain = IntentBrain(mode="pydantic_ai", agent=agent)  # type: ignore[arg-type]

    probe = brain.probe_question(GAP)

    assert probe.intent is None
    assert probe.reason == "model_failed"
    assert probe.source == "rules_fallback"
    assert probe.trace is not None
    assert probe.trace.status == "FAILED"
    assert probe.trace.failed is True


# 5. 结构断言：这条通路里不允许出现采纳面的任何入口（先例见
#    tests/test_counter_question_catalog.py 末尾的 loader 源码扫描）。


def test_the_probe_source_never_touches_the_adoption_surface() -> None:
    source = inspect.getsource(IntentBrain.probe_question)
    for forbidden in ("decide_adoption", "ModelDecision", "ADOPTABLE_INTENTS"):
        assert forbidden not in source, f"probe_question 不得引用 {forbidden}"


def test_the_probe_never_replaces_trace_count_fields() -> None:
    """``replace(trace, ...)`` 只许出现在零计数键的地方——最简单也最硬的口径：根本不用它。"""
    source = inspect.getsource(IntentBrain.probe_question)
    assert "replace(" not in source


# 6. 候选必须来自同一个纯核（口径 §4.1 禁止第二构造点），且原样透传给 agent。


def test_the_candidate_set_is_the_pure_kernel_output_passed_through_verbatim() -> None:
    agent = RecordingAgent(intent="TICKET_CREATE")
    brain = IntentBrain(mode="pydantic_ai", agent=agent)  # type: ignore[arg-type]
    context = {"last_intent": "REFUND_STATUS"}

    first = brain.probe_question(GAP, context)
    second = brain.probe_question(GAP, context)

    # 同一纯核：与直接调用 ``build_candidates`` 的产物逐字段相等。
    expected = build_candidates(
        GAP,
        context,
        classify(GAP, context),
        catalog=get_catalog(),
        order_state=None,
        max_candidates=6,
    )
    assert first.candidate_set == expected
    # 纯核保证：同一句两次问询，候选逐字节一致。
    assert first.candidate_set == second.candidate_set
    # 透传：agent 收到的候选集就是 probe 报告的那一个。
    assert agent.calls[0]["candidates"] is first.candidate_set


def test_question_probe_reasons_is_a_closed_tuple() -> None:
    assert isinstance(QUESTION_PROBE_REASONS, tuple)
    assert len(set(QUESTION_PROBE_REASONS)) == len(QUESTION_PROBE_REASONS)
    assert set(QUESTION_PROBE_REASONS) == {
        "rules_mode",
        "no_candidates",
        "model_failed",
        "model_none",
        "model_unknown",
        "model_out_of_candidates",
        "matches_rule",
    }


def test_question_probe_is_frozen() -> None:
    probe = QuestionProbe(None, None, None, "rules", "rules_mode")
    with pytest.raises(dataclasses.FrozenInstanceError):
        probe.intent = "TICKET_CREATE"  # type: ignore[misc]
