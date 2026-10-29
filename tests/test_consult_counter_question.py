"""F-1 T4b 钉桩：consult 出口的「被采纳面拒掉的合法改道读数换来一次反问」。

口径 ``.qoder/rules/clarify-counter-question.md``：§3.1 触发判据（唯一合法
实现 = 照抄 `decide_adoption` 已算好的 `redirect_rejected`）、§3.3 形成侧
落点守卫、§4.1 consult 选项经 `ModelTrace` 带内字段携带且不重算、§2.2/§8.3
两字段永不序列化。钉住的最小事实：

1. consult 触发满足时产出一次反问，路由仍是规则读数（§12.12）；
2. 选项忠实性：呈现序 = 模型实际收到的候选序（max_options 截断、UNKNOWN 跳过）（§12.14）；
3. 单轮上限：顺序第二轮 consult 轮不再反问（§12.12、§5.1）；
4. 带内字段不落盘：``as_dict`` 键集逐字不变、``from_dict({})`` 两字段 None（§12.13）；
5. 被采纳的退款读数走既有采纳路径，**不反问**（§3.2 表 consult 行）；
6. ``candidates_enabled=False`` 时 consult 出口零反问（§3.1，不凭空造候选）；
7. 入口守卫：``plan.intent`` 非 UNKNOWN（ORDER_QUERY）的 consult 轮不反问（§3.3）；
8. 既有问句门/候选/采纳回归不因本次改动变化（运行证据见交付物，命令在
   ``tests/test_counter_question_turn.py`` 等既有文件里跑）。

模型不出现：全部走 ``tests/test_intent_candidate_model`` 登记的
``RecordingAgent`` 替身 + **真实** ``IntentBrain``——新行为必须由真实
``plan`` 的采纳面与带内字段写点亲自跑一遍。``test_llm_gap`` 的手工
``FixedBrain`` 四键恒 ``None``，永远走不进本分支，禁止用它钉本批行为。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.agent.counter_question import get_counter_question
from app.agent.pydantic_agent import IntentAgent, IntentBrain, ModelTrace
from tests.conftest import ALICE
from tests.test_intent_candidate_model import RecordingAgent
from tests.test_llm_gap import _app

# 规则读成 UNKNOWN、非问句形态（走 consult 出口）、且没有任何内容证据可窄化
# 候选集的一句话：模型看到的是全目录，`TICKET_CREATE` 是合法读数、在候选集
# 内、不在采纳面里——正是 §3.2 表「判不采纳的合法非退款读数」那一行。
CONSULT_GAP = "我申请退一部分款"
# 同一会话的第二句 consult gap（与第一句分开写，排除任何复用台账的可能）。
CONSULT_GAP_2 = "东西根本没法用，我不想留了，把付的钱给我回来"
# 带订单号：对话落点是 ORDER_QUERY（§3.3 守卫的落点非 UNKNOWN 形态）。
ORDER_CONSULT_GAP = "ORD-DEMO-1001 那笔钱给我退回来"

COUNTER_REASON = "COUNTER_QUESTION_INTENT_AMBIGUOUS"
_HANDOFF_TAIL = "你也可以直接说「转人工」，我会转给人工同事接手。"

# ``actions.unknown`` 今天的兜底菜单，逐字副本（与 test_counter_question_turn
# 的常量同源）：不反问的 consult 轮必须原样落回这里。
UNKNOWN_MENU_REPLY = (
    "我可以帮你查商品信息、售后条款、订单与物流，"
    "也可以按你的订单判断退款资格，"
    "再把退款或工单请求交给确定性工作流。请补充商品名、SKU 或订单号。"
)

# T4b 落地前后 `ModelTrace.as_dict` 的完整键集（§12.13 的逐字判据）：
# 十个恒在键 + 判定发生时才写的四个计数键。带内字段不在任何一份里。
AS_DICT_KEYS = frozenset(
    {
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
        "model_adopted",
        "model_redirect",
        "model_redirect_rejected",
        "model_redirect_kind",
    }
)


def _consult_brain(intent: str, **kwargs: Any) -> tuple[IntentBrain, RecordingAgent]:
    """真实 IntentBrain + 记录型假 agent（口径 §12.12 的指定形状）。"""
    agent = RecordingAgent(intent=intent)
    brain = IntentBrain(mode="pydantic_ai", agent=cast(IntentAgent, agent), **kwargs)
    return brain, agent


def _send(client: TestClient, message: str, conversation_id: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": message, "conversation_id": conversation_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _understand(client: TestClient, trace_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/traces/{trace_id}", headers=ALICE)
    assert response.status_code == 200, response.text
    spans = [span for span in response.json()["spans"] if span["kind"] == "understand"]
    assert spans, f"no understand span in {trace_id}"
    return dict(spans[0]["attributes"])


def test_a_rejected_reroute_reading_on_the_consult_exit_asks_once(tmp_path: Path) -> None:
    """§12.12：UNKNOWN 落点 + 被拒的合法改道读数 → 一次反问，路由不变。"""
    brain, agent = _consult_brain("TICKET_CREATE")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, CONSULT_GAP, "t4b-once")

    assert len(agent.calls) == 1  # 一次 plan，没有第二次候选调用（§4.1）
    assert body["intent"] == "UNKNOWN"  # 反问不改路由（§2.1）
    clarification = body["clarification"]
    assert clarification is not None
    assert clarification["reason"] == COUNTER_REASON
    assert clarification["prompt"] == get_counter_question().prompt_template
    assert clarification["expected_slot"] is None
    assert clarification["resumable_intent"] is None
    spec = get_counter_question()
    assert 0 < len(body["suggestions"]) <= spec.max_options
    assert body["reply"] == (
        f"{spec.prompt_template}{'、'.join(body['suggestions'])}。{_HANDOFF_TAIL}"
    )
    assert body["slots"]["counter_question_asked"] is True  # §5.1 随回合写回落库


def test_the_options_are_the_very_ids_the_model_received(tmp_path: Path) -> None:
    """§12.14：呈现的选项 id 序逐字等于同一次调用传给模型的候选序。"""
    brain, agent = _consult_brain("TICKET_CREATE")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, CONSULT_GAP, "t4b-fidelity")

    received = agent.calls[0]["candidates"]
    assert received is not None  # 模型真实收到了候选集
    spec = get_counter_question()
    expected: list[str] = []
    for intent_id in received.ids():
        if len(expected) >= spec.max_options:
            break
        if intent_id == "UNKNOWN":
            continue
        expected.append(spec.labels[intent_id])
    assert body["suggestions"] == expected


def test_the_cap_blocks_the_second_consult_round(tmp_path: Path) -> None:
    """§12.12/§5.1：顺序第二轮再满足触发也不反问，逐字回落到今天的菜单。"""
    brain, _agent = _consult_brain("TICKET_CREATE")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    first = _send(client, CONSULT_GAP, "t4b-cap")
    assert first["clarification"] is not None
    assert first["slots"]["counter_question_asked"] is True

    second = _send(client, CONSULT_GAP_2, first["conversation_id"])
    assert second["clarification"] is None
    assert second["reply"] == UNKNOWN_MENU_REPLY
    assert second["slots"]["counter_question_asked"] is True  # carried 位不被清回


def test_the_in_band_fields_never_cross_the_serialization_boundary() -> None:
    """§12.13：带内字段只活在回合内存里，序列化面逐字不变。"""
    brain, agent = _consult_brain("TICKET_CREATE")

    _routed, _source, trace = brain.plan(CONSULT_GAP, {})

    assert trace is not None
    # 写给消费端的恰是「有效读数 + 模型实际收到的候选序」（§2.2）。
    assert trace.answer == "TICKET_CREATE"
    assert trace.candidate_ids == agent.calls[0]["candidates"].ids()
    # as_dict 的键集与 T4b 落地前逐字相等：没有 answer/candidate_ids 的形态。
    data = trace.as_dict()
    assert frozenset(data) == AS_DICT_KEYS
    assert data["model_redirect_rejected"] is True
    assert data["model_adopted"] is False
    # from_dict 不读它们：任何落盘形态读回来都是 None。
    revived = ModelTrace.from_dict(data)
    assert revived.answer is None and revived.candidate_ids is None
    assert ModelTrace.from_dict({}).answer is None
    assert ModelTrace.from_dict({}).candidate_ids is None


def test_an_adopted_refund_reading_still_does_not_ask(tmp_path: Path) -> None:
    """§3.2 表 consult 行：被采纳的退款读数走既有采纳路径，零反问。"""
    brain, _agent = _consult_brain("REFUND_REQUEST")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, CONSULT_GAP, "t4b-adopt")

    assert body["intent"] == "REFUND_REQUEST"  # 改路由的只有采纳面那一行
    clarification = body["clarification"]
    assert clarification is None or clarification["reason"] != COUNTER_REASON
    assert body["slots"]["counter_question_asked"] is False


def test_a_candidates_disabled_consult_round_asks_nothing(tmp_path: Path) -> None:
    """§3.1/§12.14：候选关闭 → `candidate_ids` 为 None → 不凭空造选项。

    这一轮被拒改道**照样如实计数**（`redirect_rejected` 为真），只是没有
    候选序就没有可呈现的选项——反问缺的是证据边界，不是意愿。
    """
    brain, _agent = _consult_brain("TICKET_CREATE", candidates_enabled=False)
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, CONSULT_GAP, "t4b-nocand")

    assert body["clarification"] is None
    assert body["reply"] == UNKNOWN_MENU_REPLY
    assert body["slots"]["counter_question_asked"] is False
    model = _understand(client, body["trace_id"])["model_gap"]["model"]
    assert model["model_redirect_rejected"] is True  # 计数不装假
    assert frozenset(model) <= AS_DICT_KEYS  # 带内字段没有搭 span 的车


def test_a_nonunknown_landing_never_forms_a_draft(tmp_path: Path) -> None:
    """§3.3 形成侧守卫：对话落点 ORDER_QUERY 的 consult 轮不反问。

    规则在 brain 里仍读 UNKNOWN，`ORDER_QUERY` 是合法在集读数（slot 证据），
    所以 `redirect_rejected` 为真——正因触发判据会为真，才必须靠落点守卫把
    永不可见的草稿挡在构造之前。
    """
    brain, agent = _consult_brain("ORDER_QUERY")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, ORDER_CONSULT_GAP, "t4b-landing")

    assert len(agent.calls) == 1  # 模型确实被问过这一轮
    assert body["intent"] == "ORDER_QUERY"  # 落点逐字不变（订单卡）
    assert body["clarification"] is None
    assert body["slots"]["counter_question_asked"] is False  # 上限位不被消耗
    model = _understand(client, body["trace_id"])["model_gap"]["model"]
    assert model["model_redirect_rejected"] is True  # 判定照记，反问不形成
