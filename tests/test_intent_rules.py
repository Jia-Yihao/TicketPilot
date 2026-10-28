from __future__ import annotations

import pytest

from app.agent.routing import (
    Intent,
    _match_write_intent,
    blocks_write_action,
    classify,
    is_generic_statement,
    is_interrogative,
    is_policy_question,
)
from app.agent.support_agent import SupportIntent, classify_support


def _support(text: str) -> SupportIntent:
    route = classify(text, {})
    return classify_support(
        text,
        route,
        has_catalog_match=False,
        message_order_id=route.order_id,
    ).intent


@pytest.mark.parametrize(
    "text",
    [
        "退款条件是什么",
        "签收以后多少天内可以申请退款",
        "退款申请需要谁审批",
        "退款金额应该怎么计算",
        "退款调用超时能不能直接再提交一次",
        "什么情况下要创建最高优先级的工单",
        "重复提交工单会生成好几张工单",
        "什么时候应该转人工处理",
        "回答物流问题需要哪些证据要求",
    ],
)
def test_policy_questions_never_trigger_write_actions(text: str) -> None:
    assert classify(text, {}).intent is Intent.POLICY_QA
    assert _support(text) is SupportIntent.POLICY_QA
    # F-9 阶段 B（dispatch C3 / plan T1）：写意图的词表迁进 YAML 后，问句门槛必须
    # 还在。这句的 cue 命中的是写行 TICKET_CREATE，被门槛挡住才没建成单——
    # 第三行钉「cue 确实命中」，否则门槛消失也会一路绿。
    assert classify("什么时候可以转人工", {}).intent is Intent.POLICY_QA
    assert _support("什么时候可以转人工") is SupportIntent.POLICY_QA
    assert _match_write_intent("什么时候可以转人工") is not None


@pytest.mark.parametrize(
    "text",
    [
        "我要申请退款，订单 ORD-DEMO-1001，金额 129 元",
        "帮我创建一个工单投诉",
        "我要转人工",
    ],
)
def test_explicit_requests_still_create_write_actions(text: str) -> None:
    assert not blocks_write_action(text.lower())
    # F-9 阶段 B 复审：写分支从 HEAD 的 `if _REFUND ... elif _TICKET` 改成按目录
    # priority 遍历，退款行 10 早于工单行 11 是保住那条先后次序的唯一依据。这里把
    # 「退款 + 投诉」同句钉在既有用例体内（参数化表不改）。工单行调到 <=10 即红；
    # 退款行调到 12（工单仍 11）同样红。两行都调到 11 时按 intent_id 定序，仍得退款。
    assert classify("我要申请退款，顺便投诉一下").intent is Intent.REFUND_REQUEST
    assert _support("我要申请退款，顺便投诉一下") is SupportIntent.REFUND_REQUEST


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("能不能帮我申请退款，订单 ORD-DEMO-1001", Intent.REFUND_REQUEST),
        ("申请退款 ORD-DEMO-1001", Intent.REFUND_REQUEST),
        ("请帮我创建一个工单，投诉物流延迟", Intent.TICKET_CREATE),
        ("帮我申请退款，退款规则是 v3", Intent.REFUND_REQUEST),
        ("退款流程要走哪几步", Intent.POLICY_QA),
        ("订单 ORD-DEMO-1001 能退吗", Intent.POLICY_QA),
        ("今天天气怎么样", Intent.UNKNOWN),
    ],
)
def test_request_frames_outrank_policy_terms(text: str, intent: Intent) -> None:
    assert classify(text, {}).intent is intent


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("订单 ORD-DEMO-1002 的物流到哪了", SupportIntent.LOGISTICS_QUERY),
        ("我的退款还没到账", SupportIntent.REFUND_STATUS),
        ("订单 ORD-DEMO-1002 一直没收到", SupportIntent.DELIVERY_ISSUE),
        ("订单 ORD-DEMO-1001 这单能退吗", SupportIntent.REFUND_ELIGIBILITY),
        ("什么时候可以转人工", SupportIntent.POLICY_QA),
        ("退货的时效是多久", SupportIntent.POLICY_QA),
        ("物流轨迹多久没更新算异常", SupportIntent.LOGISTICS_QUERY),
        ("能不能帮我申请退款，订单 ORD-DEMO-1001", SupportIntent.REFUND_REQUEST),
    ],
)
def test_support_intents_keep_their_precedence(text: str, intent: SupportIntent) -> None:
    assert _support(text) is intent
    # F-9 阶段 B 步骤 1：写意图的 cues 迁进 `intents.yaml`（并带 `match_request_only`）。
    # 内核证据形状变了（见 tests/test_intent_candidates.py），生产路径必须逐条钉住不变：
    # 命中 cue 的陈述句照旧进写意图，含同一批词的问句照旧不进写路径。
    assert _support("那我只能投诉") is SupportIntent.TICKET_CREATE
    assert (
        _support("顺便帮我投诉一下这个快递员态度非常恶劣谢谢了")
        is SupportIntent.TICKET_CREATE
    )
    assert _support("帮我退款 ORD-DEMO-1001 129 元") is SupportIntent.REFUND_REQUEST
    assert _support("退款申请需要谁审批") is SupportIntent.POLICY_QA
    assert _support("什么时候可以转人工") is SupportIntent.POLICY_QA


@pytest.mark.parametrize(
    ("text", "interrogative", "generic", "policy"),
    [
        ("退款条件是什么", True, False, True),
        ("重复提交工单会生成好几张工单", False, True, True),
        ("我要申请退款", False, False, False),
        ("今天天气怎么样", True, False, False),
        ("帮我创建一个工单，投诉物流延迟", False, False, False),
    ],
)
def test_speech_act_helpers(
    text: str, interrogative: bool, generic: bool, policy: bool
) -> None:
    lowered = text.lower()
    assert is_interrogative(lowered) is interrogative
    assert is_generic_statement(lowered) is generic
    assert is_policy_question(lowered) is policy
