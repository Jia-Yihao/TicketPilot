from __future__ import annotations

from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.time import utcnow
from app.models.commerce import Order, Refund
from app.models.enums import OrderStatus, RefundStatus
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
