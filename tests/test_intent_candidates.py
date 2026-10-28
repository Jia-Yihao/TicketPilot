"""P8 候选内核：服务端用证据算出「模型能说什么」。

这些断言全部对着真实目录（``intents.yaml``）与真实规则读数（``classify``）跑，
不 mock：候选化的价值就是「每条候选都能回指一个服务端自己算出来的事实」，
用假目录测它等于没测。
"""

from __future__ import annotations

from pathlib import Path

from app.agent.candidates import build_candidates, describe_candidates
from app.agent.intent_catalog import get_catalog
from app.agent.routing import classify
from app.core.config import get_settings

CATALOG = get_catalog()
REPO_ROOT = Path(__file__).resolve().parents[1]


def _route(text: str):
    return classify(text)


def test_the_same_input_produces_the_same_candidates_in_the_same_order() -> None:
    message = "我的订单 ORD-DEMO-1001 的物流到哪了"
    route = _route(message)

    first = build_candidates(message, {}, route, catalog=CATALOG)
    second = build_candidates(message, {}, route, catalog=CATALOG)

    assert first.candidates == second.candidates
    # priority 升序：HUMAN_HANDOFF(5) < LOGISTICS_QUERY(60) < ORDER_QUERY(80) < UNKNOWN(999)
    assert first.ids() == (
        "HUMAN_HANDOFF",
        "LOGISTICS_QUERY",
        "ORDER_QUERY",
        "UNKNOWN",
    )


def test_a_rule_reading_and_its_cues_are_recorded_in_a_fixed_evidence_order() -> None:
    message = "ORD-DEMO-1001 的物流到哪了"
    route = _route(message)
    assert route.intent.value == "LOGISTICS_QUERY"

    candidate_set = build_candidates(message, {}, route, catalog=CATALOG)

    logistics = next(c for c in candidate_set.candidates if c.intent_id == "LOGISTICS_QUERY")
    assert logistics.evidence == (
        "rule:LOGISTICS_QUERY",
        "cue:物流",
        "cue:到哪",
        "slot:order_id",
    )
    assert logistics.bucket == "high"
    assert candidate_set.ids() == ("HUMAN_HANDOFF", "LOGISTICS_QUERY", "UNKNOWN")


def test_a_write_row_is_admitted_by_its_rule_reading_and_its_own_cue() -> None:
    """F-9 步骤 1 之前，这一行**一条 cue 都没有**，只有 ``rule:`` 一条证据。

    写意图的 cues 迁进目录之后，同一句话多出一条 ``cue:``，于是证据种类数从 1 变 2、
    桶从 medium 变 high。这是候选内核（模型面）的证据形状，不是判定：这句话的
    ``classify`` 读数与端到端 ``SupportPlan`` 逐字未变（见
    ``tests/test_intent_rules.py::test_support_intents_keep_their_precedence``）。
    """
    message = "我要退款"
    route = _route(message)
    assert route.intent.value == "REFUND_REQUEST"

    candidate_set = build_candidates(message, {}, route, catalog=CATALOG)

    refund = next(c for c in candidate_set.candidates if c.intent_id == "REFUND_REQUEST")
    assert refund.evidence == ("rule:REFUND_REQUEST", "cue:我要退款")
    assert refund.bucket == "high"


def test_at_most_three_cues_are_recorded_in_catalog_order() -> None:
    message = "物流 快递 运单 配送 发货"

    candidate_set = build_candidates(message, {}, _route(message), catalog=CATALOG)

    logistics = next(c for c in candidate_set.candidates if c.intent_id == "LOGISTICS_QUERY")
    assert tuple(e for e in logistics.evidence if e.startswith("cue:")) == (
        "cue:物流",
        "cue:快递",
        "cue:运单",
    )


def test_slots_are_evidence_only_when_they_really_exist() -> None:
    message = "我要退款 ORD-DEMO-1001 129 元"

    candidate_set = build_candidates(message, {}, _route(message), catalog=CATALOG)

    refund = next(c for c in candidate_set.candidates if c.intent_id == "REFUND_REQUEST")
    assert refund.evidence == (
        "rule:REFUND_REQUEST",
        "cue:我要退款",
        "slot:order_id",
        "slot:amount_cents",
    )
    assert refund.bucket == "high"
    # 一个没有任何依据的行不得因为「模型可能想选」而进候选。
    assert "TICKET_CREATE" not in candidate_set.ids()
    assert "DELIVERY_ISSUE" not in candidate_set.ids()


def test_an_order_id_alone_does_not_narrow_anything() -> None:
    """订单号只说明「凡是要求订单号的意图都可能」，那等于没有依据。

    没有内容依据时候选集**不收窄**：模型看到整份目录的行名，但一个候选都不许自称有依据。
    """
    message = "ORD-DEMO-1001"

    candidate_set = build_candidates(message, {}, _route(message), catalog=CATALOG)

    assert candidate_set.narrowed is False
    assert candidate_set.ids() == tuple(
        spec.intent_id for spec in CATALOG.ordered_specs()
    )
    assert all(candidate.bucket == "low" for candidate in candidate_set.candidates)
    assert all(
        not any(e.split(":", 1)[0] in {"rule", "cue", "context"} for e in candidate.evidence)
        for candidate in candidate_set.candidates
    )


def test_a_conversation_context_admits_only_the_row_it_names() -> None:
    message = "这个怎么处理一下"
    context = {"pending_intent": "REFUND_STATUS", "last_intent": "LOGISTICS_QUERY"}

    candidate_set = build_candidates(message, context, _route(message), catalog=CATALOG)

    assert candidate_set.ids() == (
        "HUMAN_HANDOFF",
        "REFUND_STATUS",
        "LOGISTICS_QUERY",
        "UNKNOWN",
    )
    pending = next(c for c in candidate_set.candidates if c.intent_id == "REFUND_STATUS")
    assert pending.evidence == ("context:pending_intent",)
    assert pending.bucket == "low"
    previous = next(c for c in candidate_set.candidates if c.intent_id == "LOGISTICS_QUERY")
    assert previous.evidence == ("context:last_intent",)


def test_the_order_state_is_recorded_only_when_the_caller_supplied_it() -> None:
    message = "ORD-DEMO-1001 的物流到哪了"
    route = _route(message)

    without = build_candidates(message, {}, route, catalog=CATALOG)
    with_state = build_candidates(message, {}, route, catalog=CATALOG, order_state="DELIVERED")
    state_alone = build_candidates(
        "ORD-DEMO-1001", {}, _route("ORD-DEMO-1001"), catalog=CATALOG, order_state="DELIVERED"
    )

    assert all(
        not any(e.startswith("order:") for e in candidate.evidence)
        for candidate in without.candidates
    )
    logistics = next(c for c in with_state.candidates if c.intent_id == "LOGISTICS_QUERY")
    assert logistics.evidence[-1] == "order:DELIVERED"
    assert logistics.bucket == "high"
    # 订单状态是加强证据：它自己不收窄候选集，只把事实挂在已经入选的行上。
    assert state_alone.narrowed is False
    assert all(candidate.bucket == "low" for candidate in state_alone.candidates)
    unscoped = next(c for c in state_alone.candidates if c.intent_id == "POLICY_QA")
    assert not any(e.startswith("order:") for e in unscoped.evidence)


def test_a_message_with_no_signal_shows_the_whole_catalog_without_faking_evidence() -> None:
    """没有依据就不装作有依据：候选集不收窄，但也没有任何一行自称带内容证据。"""
    message = "在吗在吗"

    candidate_set = build_candidates(message, {}, _route(message), catalog=CATALOG)

    assert candidate_set.narrowed is False
    assert {"HUMAN_HANDOFF", "UNKNOWN"} <= set(candidate_set.ids())
    assert len(candidate_set.ids()) == len(CATALOG.specs)
    assert {c.bucket for c in candidate_set.candidates} == {"low"}


def test_truncation_prefers_the_higher_bucket_then_the_catalog_priority() -> None:
    """这句话里 POLICY_QA 同时拿到规则读数与 cue，其他行只有 cue。"""
    message = "退款 物流 政策 商品 订单"
    route = _route(message)
    assert route.intent.value == "POLICY_QA"

    full = build_candidates(message, {}, route, catalog=CATALOG)
    cut = build_candidates(message, {}, route, catalog=CATALOG, max_candidates=2)

    assert [c.bucket for c in full.candidates if c.intent_id == "POLICY_QA"] == ["high"]
    assert cut.ids() == ("HUMAN_HANDOFF", "POLICY_QA", "LOGISTICS_QUERY", "UNKNOWN")
    assert "PRODUCT_QUERY" not in cut.ids()
    assert "ORDER_QUERY" not in cut.ids()


def test_handoff_and_unknown_are_never_truncated_away() -> None:
    """上限只管「有依据的行」：兜底两行永远在，也不占这个额度。"""
    message = "退款 物流 政策 商品 订单"
    route = _route(message)

    room = build_candidates(message, {}, route, catalog=CATALOG)
    tight = build_candidates(message, {}, route, catalog=CATALOG, max_candidates=2)

    assert len([i for i in room.ids() if i not in {"HUMAN_HANDOFF", "UNKNOWN"}]) > 2
    assert "HUMAN_HANDOFF" in tight.ids()
    assert "UNKNOWN" in tight.ids()
    assert len([i for i in tight.ids() if i not in {"HUMAN_HANDOFF", "UNKNOWN"}]) == 2


def test_the_candidate_set_allows_only_its_candidates_and_none_is_not_one() -> None:
    message = "ORD-DEMO-1001 的物流到哪了"

    candidate_set = build_candidates(message, {}, _route(message), catalog=CATALOG)

    assert candidate_set.allow_none is True
    assert candidate_set.allows("LOGISTICS_QUERY") is True
    assert candidate_set.allows("NONE") is False
    assert candidate_set.allows("PRODUCT_QUERY") is False
    assert candidate_set.bucket_of("LOGISTICS_QUERY") == "high"
    assert candidate_set.bucket_of("PRODUCT_QUERY") is None
    assert all(isinstance(intent_id, str) for intent_id in candidate_set.ids())


def test_the_l2_definitions_are_available_on_demand_but_only_for_candidates() -> None:
    message = "我要退款 ORD-DEMO-1001 129 元"

    candidate_set = build_candidates(message, {}, _route(message), catalog=CATALOG)
    described = describe_candidates(candidate_set, catalog=CATALOG)

    assert [item["intent_id"] for item in described] == list(candidate_set.ids())
    refund = next(item for item in described if item["intent_id"] == "REFUND_REQUEST")
    assert refund["validation"] == "eligibility_and_approval"
    assert refund["required_slots"] == ("order_id", "amount_cents", "refund_reason")
    assert refund["fallback"] == "approval"
    # L2 是目录行的定义，不是整份目录：未入选的行不在里面。
    assert "PRODUCT_QUERY" not in {item["intent_id"] for item in described}


def test_the_candidate_flags_default_to_on_with_a_cap_of_six() -> None:
    settings = get_settings()

    assert settings.intent_candidates_enabled is True
    assert settings.intent_candidates_max == 6


def test_both_candidate_keys_are_documented_in_the_env_example() -> None:
    """配置项只写在 Settings 里等于没法回退：开关必须能被运维看见。"""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "INTENT_CANDIDATES_ENABLED=true" in text
    assert "INTENT_CANDIDATES_MAX=6" in text
