from __future__ import annotations

import pytest

from app.agent.routing import (
    Intent,
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
