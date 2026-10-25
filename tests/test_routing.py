from __future__ import annotations

import pytest

from app.agent.routing import classify
from app.models.enums import Intent


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("我要申请退款，订单 ORD-DEMO-1001，金额 129 元", Intent.REFUND_REQUEST),
        ("退款条件是什么？", Intent.POLICY_QA),
        ("我为什么不能退款？", Intent.POLICY_QA),
        ("帮我查订单 ORD-DEMO-1001", Intent.ORDER_QUERY),
        ("订单 ORD-DEMO-1002 的物流延迟了", Intent.LOGISTICS_QUERY),
        ("帮我创建一个工单投诉", Intent.TICKET_CREATE),
        ("今天天气怎么样？", Intent.UNKNOWN),
    ],
)
def test_intent_classification(text: str, intent: Intent) -> None:
    assert classify(text).intent is intent


def test_order_id_and_amount_are_extracted() -> None:
    plan = classify("我要退款，订单 ord-demo-1001，金额 129.5 元")

    assert plan.intent is Intent.REFUND_REQUEST
    assert plan.order_id == "ORD-DEMO-1001"
    assert plan.amount_cents == 12950
    assert plan.amount_invalid is False


def test_context_order_id_is_used_as_fallback() -> None:
    plan = classify("我要退款", {"order_id": "ord-demo-1001"})

    assert plan.intent is Intent.REFUND_REQUEST
    assert plan.order_id == "ORD-DEMO-1001"


def test_invalid_amount_is_flagged() -> None:
    plan = classify("我要退款，金额 0 元")

    assert plan.amount_invalid is True


def test_refund_eligibility_question_has_priority_over_action() -> None:
    plan = classify("订单 ORD-DEMO-1001 是否符合退款条件？")

    assert plan.intent is Intent.POLICY_QA
