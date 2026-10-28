"""P11 澄清候选降级：本人最近订单的读路径、候选卡片与 trace 隐私。

候选是个人信息：只允许出现在这一次回复里。这些用例同时钉住两件事——
客户能点到的候选必须是他自己的订单，而链路账本里一个订单号都不许留。
"""

from __future__ import annotations

import json
from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.commerce import Order
from app.repositories.commerce import CommerceRepository
from tests.conftest import ALICE, BOB, CAROL


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


def _add_order(app: FastAPI, order: Order) -> None:
    with app.state.session_factory() as session:
        session.add(order)
        session.commit()


# ------------------------------------------------------------------ 仓储读路径
def test_recent_orders_are_scoped_to_tenant_and_user(app: FastAPI) -> None:
    with app.state.session_factory() as session:
        repo = CommerceRepository(session)

        alice = repo.list_recent_orders("demo", "alice", limit=10)
        bob = repo.list_recent_orders("demo", "bob", limit=10)
        carol = repo.list_recent_orders("acme", "carol", limit=10)

    assert {order.id for order in alice} >= {"ORD-DEMO-1001", "ORD-DEMO-1002"}
    assert all(order.user_id == "alice" and order.tenant_id == "demo" for order in alice)
    assert [order.id for order in bob] == ["ORD-DEMO-2001"]
    assert [order.id for order in carol] == ["ORD-OTHER-3001"]


def test_recent_orders_come_back_newest_first_and_stop_at_the_limit(
    app: FastAPI,
) -> None:
    with app.state.session_factory() as session:
        repo = CommerceRepository(session)
        rows = repo.list_recent_orders("demo", "alice", limit=10)

    assert [row.paid_at for row in rows] == sorted(
        (row.paid_at for row in rows), reverse=True
    )

    with app.state.session_factory() as session:
        limited = CommerceRepository(session).list_recent_orders(
            "demo", "alice", limit=1
        )

    assert len(limited) == 1
    assert limited[0].id == rows[0].id


def test_a_tied_payment_time_falls_back_to_the_order_id(app: FastAPI) -> None:
    from app.core.time import utcnow

    paid_at = utcnow()
    _add_order(
        app,
        Order(
            id="ORD-TIE-B",
            tenant_id="demo",
            user_id="alice",
            status="SHIPPED",
            total_cents=100,
            refundable_cents=100,
            currency="CNY",
            paid_at=paid_at,
        ),
    )
    _add_order(
        app,
        Order(
            id="ORD-TIE-A",
            tenant_id="demo",
            user_id="alice",
            status="SHIPPED",
            total_cents=100,
            refundable_cents=100,
            currency="CNY",
            paid_at=paid_at,
        ),
    )

    with app.state.session_factory() as session:
        rows = CommerceRepository(session).list_recent_orders("demo", "alice", limit=2)

    assert [row.id for row in rows] == ["ORD-TIE-B", "ORD-TIE-A"]


# ------------------------------------------------------------------ 候选卡片
def test_the_first_clarification_offers_no_candidates(client: TestClient) -> None:
    body = _message(client, ALICE, "帮我查一下订单")

    assert body["clarification"]["expected_slot"] == "order_id"
    assert body["context_cards"] == []


def test_the_second_clarification_lists_the_callers_own_orders(
    client: TestClient,
) -> None:
    first = _message(client, ALICE, "帮我查一下订单")

    second = _message(
        client, ALICE, "我忘了单号", conversation_id=first["conversation_id"]
    )

    cards = [card for card in second["context_cards"] if card["kind"] == "order_candidate"]
    assert 0 < len(cards) <= 3
    assert {card["card_id"] for card in cards} <= {
        "ORD-DEMO-1001",
        "ORD-DEMO-1002",
    }
    assert cards[0]["card_id"] == "ORD-DEMO-1002"
    for order_id in {card["card_id"] for card in cards}:
        assert order_id in second["suggestions"]


def test_candidate_cards_carry_only_l1_facts(client: TestClient) -> None:
    first = _message(client, ALICE, "帮我查一下订单")

    second = _message(
        client, ALICE, "我忘了单号", conversation_id=first["conversation_id"]
    )

    card = next(
        card for card in second["context_cards"] if card["kind"] == "order_candidate"
    )
    assert set(card["fields"]) == {
        "order_id",
        "product_name",
        "status",
        "delivered_at",
    }
    assert card["sections"] == []
    assert card["total_chars"] == 0
    assert "total_cents" not in json.dumps(card, ensure_ascii=False)
    assert "refundable_cents" not in json.dumps(card, ensure_ascii=False)


def test_candidate_cards_name_the_product_from_the_catalog(client: TestClient) -> None:
    first = _message(client, ALICE, "帮我查一下订单")

    second = _message(
        client, ALICE, "我忘了单号", conversation_id=first["conversation_id"]
    )

    by_id = {
        card["card_id"]: card
        for card in second["context_cards"]
        if card["kind"] == "order_candidate"
    }
    assert by_id["ORD-DEMO-1001"]["fields"]["product_name"]
    assert by_id["ORD-DEMO-1001"]["subtitle"] == by_id["ORD-DEMO-1001"]["fields"][
        "product_name"
    ]
    assert by_id["ORD-DEMO-1002"]["fields"]["delivered_at"] is None
    assert "未签收" in by_id["ORD-DEMO-1002"]["summary"]


def test_candidates_never_leak_another_users_orders(client: TestClient) -> None:
    """同租户的另一个用户、另一个租户的用户，都拿不到别人的单号。"""
    bob_first = _message(client, BOB, "帮我查一下订单")
    bob_second = _message(
        client, BOB, "我忘了单号", conversation_id=bob_first["conversation_id"]
    )

    carol_first = _message(client, CAROL, "帮我查一下订单")
    carol_second = _message(
        client, CAROL, "我忘了单号", conversation_id=carol_first["conversation_id"]
    )

    def ids(body: dict) -> set[str]:
        return {
            card["card_id"]
            for card in body["context_cards"]
            if card["kind"] == "order_candidate"
        }

    assert ids(bob_second) == {"ORD-DEMO-2001"}
    assert ids(carol_second) == {"ORD-OTHER-3001"}


def test_a_customer_without_orders_gets_no_candidates(
    app_factory: Callable[..., FastAPI],
) -> None:
    app = app_factory()
    client = TestClient(app)
    headers = {"X-Tenant-Id": "demo", "X-User-Id": "nobody"}

    first = _message(client, headers, "帮我查一下订单")
    second = _message(client, headers, "我忘了单号", conversation_id=first["conversation_id"])

    assert second["clarification"]["expected_slot"] == "order_id"
    assert second["context_cards"] == []
    assert second["suggestions"] == first["suggestions"]


def test_a_missing_amount_clarification_still_has_no_candidates(
    client: TestClient,
) -> None:
    first = _message(client, ALICE, "我要申请退款 129 元，质量有问题")

    assert first["clarification"]["expected_slot"] == "order_id"

    second = _message(
        client, ALICE, "我忘了单号", conversation_id=first["conversation_id"]
    )
    # 第二次缺的是订单号，所以有候选；补上订单号后缺金额，不该再列订单。
    assert any(
        card["kind"] == "order_candidate" for card in second["context_cards"]
    )

    third = _message(
        client,
        ALICE,
        "ORD-DEMO-1001",
        conversation_id=first["conversation_id"],
    )
    assert third["clarification"] is None
    assert third["task"] is not None

    other = _message(client, ALICE, "我要申请退款，订单 ORD-DEMO-1001")
    assert other["clarification"]["expected_slot"] == "amount_cents"
    assert other["context_cards"] == []

    resume = _message(
        client, ALICE, "我忘了金额", conversation_id=other["conversation_id"]
    )
    assert resume["clarification"]["expected_slot"] == "amount_cents"
    assert [
        card["kind"] for card in resume["context_cards"]
    ] == []


def test_choosing_a_candidate_resumes_the_interrupted_intent(
    client: TestClient,
) -> None:
    first = _message(client, ALICE, "我的退款还没到账")
    second = _message(
        client, ALICE, "我忘了单号", conversation_id=first["conversation_id"]
    )

    card = next(
        card for card in second["context_cards"] if card["kind"] == "order_candidate"
    )
    third = _message(
        client, ALICE, card["card_id"], conversation_id=first["conversation_id"]
    )

    assert third["intent"] == "REFUND_STATUS"
    assert third["clarification"] is None
    assert third["slots"]["order_id"] == card["card_id"]


# ------------------------------------------------------------------ trace 隐私
def test_the_trace_counts_candidates_without_ids(client: TestClient) -> None:
    first = _message(client, ALICE, "帮我查一下订单")

    second = _message(
        client, ALICE, "我忘了单号", conversation_id=first["conversation_id"]
    )

    response = client.get(
        f"/api/v1/traces/{second['trace_id']}", headers=ALICE
    )
    assert response.status_code == 200, response.text
    trace = response.json()
    payload = json.dumps(trace, ensure_ascii=False)

    span = next(span for span in trace["spans"] if span["name"] == "support.reply")
    cards = [
        card for card in second["context_cards"] if card["kind"] == "order_candidate"
    ]
    assert span["attributes"]["clarify_candidates"] == len(cards)
    for card in cards:
        assert card["card_id"] not in payload
