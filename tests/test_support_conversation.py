from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.time import utcnow
from app.models.commerce import Order, Refund
from app.models.conversation import ConversationMessage
from app.models.enums import OrderStatus, RefundStatus
from app.services.conversations import ConversationSlots
from tests.conftest import ALICE, CAROL


def _message(
    client: TestClient,
    headers: dict[str, str],
    text: str,
    **extra: object,
) -> dict:
    response = client.post(
        "/api/v1/support/messages",
        headers=headers,
        json={"message": text, **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _seed_order(app: FastAPI, order: Order) -> None:
    with app.state.session_factory() as session:
        session.add(order)
        session.commit()


def test_order_slot_is_carried_into_the_next_turn(client: TestClient) -> None:
    first = _message(client, ALICE, "订单 ORD-DEMO-1001 买的是什么商品？")

    second = _message(
        client, ALICE, "物流到哪了？", conversation_id=first["conversation_id"]
    )

    assert second["intent"] == "LOGISTICS_QUERY"
    assert second["slots"]["order_id"] == "ORD-DEMO-1001"
    assert second["inherited_slots"] == ["order_id"]
    assert second["context_cards"][0]["card_id"] == "ORD-DEMO-1001"


def test_missing_order_id_is_asked_once_and_then_resumed(client: TestClient) -> None:
    first = _message(client, ALICE, "我的退款还没到账")

    assert first["intent"] == "REFUND_STATUS"
    assert first["context_cards"] == []
    assert first["clarification"] is not None
    assert first["clarification"]["reason"] == "MISSING_ORDER_ID"
    assert first["clarification"]["expected_slot"] == "order_id"
    assert first["clarification"]["resumable_intent"] == "REFUND_STATUS"

    second = _message(
        client, ALICE, "ORD-DEMO-1001", conversation_id=first["conversation_id"]
    )

    assert second["intent"] == "REFUND_STATUS"
    assert second["clarification"] is None
    assert second["slots"]["order_id"] == "ORD-DEMO-1001"
    assert second["slots"]["pending_intent"] is None
    assert second["context_cards"][0]["card_id"] == "ORD-DEMO-1001"


def test_refund_request_asks_for_the_order_before_creating_a_task(
    client: TestClient,
) -> None:
    first = _message(client, ALICE, "我要申请退款，质量有问题")

    assert first["intent"] == "REFUND_REQUEST"
    assert first["task"] is None
    assert first["clarification"] is not None
    assert first["clarification"]["reason"] == "MISSING_ORDER_ID"
    assert client.get("/api/v1/tasks", headers=ALICE).json()["total"] == 0

    second = _message(
        client, ALICE, "ORD-DEMO-1001 129 元", conversation_id=first["conversation_id"]
    )

    assert second["intent"] == "REFUND_REQUEST"
    assert second["clarification"] is None
    assert second["task"] is not None
    assert second["task"]["status"] == "WAITING_APPROVAL"
    assert second["task"]["pending_approval"]["amount_cents"] == 12900
    assert client.get("/api/v1/tasks", headers=ALICE).json()["total"] == 1


def test_refund_request_without_an_amount_asks_before_creating_a_task(
    client: TestClient,
) -> None:
    first = _message(client, ALICE, "我要申请退款，订单 ORD-DEMO-1001，质量有问题")

    assert first["intent"] == "REFUND_REQUEST"
    assert first["task"] is None
    assert first["clarification"] is not None
    assert first["clarification"]["reason"] == "MISSING_AMOUNT"
    assert first["clarification"]["expected_slot"] == "amount_cents"
    assert client.get("/api/v1/tasks", headers=ALICE).json()["total"] == 0

    second = _message(
        client, ALICE, "129 元", conversation_id=first["conversation_id"]
    )

    assert second["clarification"] is None
    assert second["task"] is not None
    assert second["task"]["pending_approval"]["amount_cents"] == 12900
    assert client.get("/api/v1/tasks", headers=ALICE).json()["total"] == 1


def test_amount_slot_is_carried_into_the_resumed_refund_request(
    client: TestClient,
) -> None:
    first = _message(client, ALICE, "我要申请退款 129 元，质量有问题")

    assert first["slots"]["amount_cents"] == 12900
    assert first["clarification"]["reason"] == "MISSING_ORDER_ID"

    second = _message(
        client, ALICE, "ORD-DEMO-1001", conversation_id=first["conversation_id"]
    )

    assert second["slots"]["amount_cents"] == 12900
    assert second["task"]["pending_approval"]["amount_cents"] == 12900


def test_refund_reason_is_carried_into_the_resumed_refund_request(
    client: TestClient,
) -> None:
    # The customer answered the reason on the first turn; when the second
    # turn only supplies the amount, the carried reason must not be asked
    # for again (MISSING_REFUND_REASON would be a product defect here).
    first = _message(client, ALICE, "我要申请退款，订单 ORD-DEMO-1001，商品有质量问题")

    assert first["intent"] == "REFUND_REQUEST"
    assert first["task"] is None
    assert first["clarification"] is not None
    assert first["clarification"]["reason"] == "MISSING_AMOUNT"
    assert first["clarification"]["expected_slot"] == "amount_cents"

    second = _message(
        client, ALICE, "129 元", conversation_id=first["conversation_id"]
    )

    assert second["clarification"] is None
    assert second["task"] is not None
    assert second["task"]["pending_approval"]


def test_refund_eligibility_gives_a_personal_verdict(client: TestClient) -> None:
    body = _message(client, ALICE, "订单 ORD-DEMO-1001 能退吗")

    assert body["intent"] == "REFUND_ELIGIBILITY"
    assert body["task"] is None
    assert "refund-policy-v3" in body["reply"]
    assert "129.00" in body["reply"]
    assert body["context_cards"][0]["kind"] == "order"


def test_refund_eligibility_explains_the_blocking_reason(
    client: TestClient, app: FastAPI
) -> None:
    _seed_order(
        app,
        Order(
            id="ORD-DEMO-9001",
            tenant_id="demo",
            user_id="alice",
            status=OrderStatus.DELIVERED,
            total_cents=9900,
            refundable_cents=9900,
            currency="CNY",
            paid_at=utcnow() - timedelta(days=45),
            delivered_at=utcnow() - timedelta(days=40),
        ),
    )

    body = _message(client, ALICE, "订单 ORD-DEMO-9001 能退吗")

    assert body["intent"] == "REFUND_ELIGIBILITY"
    assert "已超过 30 天退货窗口" in body["reply"]


def test_order_reply_carries_the_eligibility_verdict(client: TestClient) -> None:
    body = _message(client, ALICE, "查询订单 ORD-DEMO-1001")

    assert body["intent"] == "ORDER_QUERY"
    assert "refund-policy-v3" in body["reply"]
    assert "129.00" in body["reply"]
    assert body["context_cards"][0]["kind"] == "order"


def test_refund_status_explains_an_unknown_refund_state(
    client: TestClient, app: FastAPI
) -> None:
    with app.state.session_factory() as session:
        session.add(
            Refund(
                id="ref_9001",
                order_id="ORD-DEMO-1001",
                tenant_id="demo",
                user_id="alice",
                amount_cents=12900,
                status=RefundStatus.UNKNOWN,
                provider="mock",
                idempotency_key="refund:unknown-9001",
            )
        )
        session.commit()

    body = _message(client, ALICE, "我的退款还没到账，订单 ORD-DEMO-1001")

    assert body["intent"] == "REFUND_STATUS"
    assert "状态待确认" in body["reply"]
    assert "对账" in body["reply"]


def test_delivery_issue_reports_logistics_and_the_delay_policy(
    client: TestClient,
) -> None:
    body = _message(client, ALICE, "订单 ORD-DEMO-1002 一直没收到")

    assert body["intent"] == "DELIVERY_ISSUE"
    assert "48" in body["reply"]
    kinds = [card["kind"] for card in body["context_cards"]]
    assert "order" in kinds
    assert "policy" in kinds


def test_conversation_of_another_user_is_not_inherited(client: TestClient) -> None:
    first = _message(client, ALICE, "订单 ORD-DEMO-1001 买的是什么商品？")

    foreign = _message(
        client, CAROL, "物流到哪了？", conversation_id=first["conversation_id"]
    )

    assert foreign["conversation_id"] != first["conversation_id"]
    assert foreign["slots"]["order_id"] is None
    assert foreign["context_cards"] == []
    assert foreign["clarification"] is not None


def test_support_answer_does_not_create_a_task(client: TestClient) -> None:
    _message(client, ALICE, "订单 ORD-DEMO-1001 能退吗")
    _message(client, ALICE, "退款条件是什么？")

    assert client.get("/api/v1/tasks", headers=ALICE).json()["total"] == 0


# ----------------------------------------------------------- R13：outcome 值域
def test_every_recorded_outcome_is_an_enum_value(
    client: TestClient, app: FastAPI
) -> None:
    """五种形态各走一遍：落库的每个 outcome 必须落在 Outcome 值域里。"""
    from app.models.enums import Outcome

    takeover = _message(client, ALICE, "转人工")
    _message(
        client, ALICE, "订单 ORD-DEMO-1001 到哪了", conversation_id=takeover["conversation_id"]
    )
    answered = _message(client, ALICE, "订单 ORD-DEMO-1001 买的是什么商品？")
    clarified = _message(client, ALICE, "我要申请退款，质量有问题")
    unresolved = _message(client, ALICE, "订单 ORD-NOPE-0000 到哪了")

    conversation_ids = {
        body["conversation_id"] for body in (takeover, answered, clarified, unresolved)
    }
    with app.state.session_factory() as session:
        rows = session.scalars(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id.in_(conversation_ids),
                ConversationMessage.role == "user",
            )
        ).all()

    seen = {
        str(message.payload["outcome"])
        for message in rows
        if message.payload and "outcome" in message.payload
    }
    assert seen <= {member.value for member in Outcome}
    assert seen == {"HANDOFF", "AGENT_SILENT", "ANSWERED", "CLARIFIED", "UNRESOLVED"}


def test_support_agent_source_has_no_bare_outcome_literals() -> None:
    """静态扫描：结案状态只能由 ``_outcome()`` 产出，源码里不许再有裸字面量。

    只扫 ``app/agent/support_agent.py``——本测试文件自己就写着这些字面量。
    """
    source = (
        Path(__file__).resolve().parents[1] / "app" / "agent" / "support_agent.py"
    ).read_text(encoding="utf-8")

    for literal in ('"HANDOFF"', '"CLARIFIED"', '"UNRESOLVED"', '"ANSWERED"', '"AGENT_SILENT"'):
        assert literal not in source, f"bare outcome literal {literal} left in support_agent.py"


# ------------------------------------------- 单轮反问上限：槽位状态位与兼容
def test_counter_question_asked_defaults_to_false() -> None:
    """新建会话的槽位：状态位在对象层与 dict 层都是 False。"""
    slots = ConversationSlots()

    assert slots.counter_question_asked is False
    assert slots.to_dict()["counter_question_asked"] is False


def test_counter_question_asked_survives_the_json_round_trip() -> None:
    """True / False 两个方向都能过 JSON 往返；全键位不丢。"""
    for value in (True, False):
        slots = ConversationSlots(
            order_id="ORD-DEMO-1001", counter_question_asked=value
        )

        payload = json.loads(json.dumps(slots.to_dict()))

        assert payload["counter_question_asked"] is value
        restored = ConversationSlots.from_dict(payload)
        assert restored.counter_question_asked is value
        assert restored.to_dict() == slots.to_dict()


def test_legacy_slot_rows_read_counter_question_asked_as_false_not_none() -> None:
    """历史行没有这个键：读回 False，不是 None，其他字段照常读出。"""
    empty = ConversationSlots.from_dict({})

    assert empty.counter_question_asked is False
    assert empty.counter_question_asked is not None
    assert ConversationSlots.from_dict(None).counter_question_asked is False
    assert (
        ConversationSlots.from_dict({"counter_question_asked": None}).counter_question_asked
        is False
    )

    legacy = ConversationSlots.from_dict(
        {
            "order_id": "ORD-DEMO-1001",
            "amount_cents": 12900,
            "refund_reason": "QUALITY",
            "failed_intents": {"LOGISTICS_QUERY": 2},
            "clarification_count": 1,
            "resolved": True,
        }
    )

    assert legacy.order_id == "ORD-DEMO-1001"
    assert legacy.amount_cents == 12900
    assert legacy.refund_reason == "QUALITY"
    assert legacy.failed_intents == {"LOGISTICS_QUERY": 2}
    assert legacy.clarification_count == 1
    assert legacy.resolved is True
    assert legacy.counter_question_asked is False
    # 旧行被重新写回时，状态位以 False 进入全键 dict，不留缺键。
    assert legacy.to_dict()["counter_question_asked"] is False


def test_support_turn_exposes_the_counter_question_asked_slot(
    client: TestClient,
) -> None:
    """回合响应的 slots 里能看到这个键，且普通回合保持 False（本任务不置位）。"""
    first = _message(client, ALICE, "订单 ORD-DEMO-1001 到哪了")

    assert first["slots"]["counter_question_asked"] is False

    second = _message(
        client, ALICE, "物流到哪了？", conversation_id=first["conversation_id"]
    )

    assert second["slots"]["counter_question_asked"] is False
