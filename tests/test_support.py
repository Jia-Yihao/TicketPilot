from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.conftest import ALICE, CAROL, REVIEWER


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


def test_product_question_returns_compact_card_without_task(
    client: TestClient,
) -> None:
    body = _message(client, ALICE, "订单 ORD-DEMO-1001 买的是什么商品？")

    assert body["intent"] == "PRODUCT_QUERY"
    assert body["agent_mode"] == "local"
    assert body["task"] is None
    assert "AeroDesk" in body["reply"]
    card = body["context_cards"][0]
    assert card["kind"] == "product"
    assert card["card_id"] == "PROD-AD-1001"
    assert card["fields"]["sku"] == "SKU-AD-1001"
    assert len(card["sections"]) == 5
    assert card["total_chars"] > 500
    assert card["sections"][2]["preview"]
    assert "私自拆解桌架" not in json.dumps(card, ensure_ascii=False)
    assert client.get("/api/v1/tasks", headers=ALICE).json()["total"] == 0


def test_product_section_is_loaded_on_demand(client: TestClient) -> None:
    card = _message(client, ALICE, "AeroDesk 的保修多久？")["context_cards"][0]
    section_id = next(
        section["id"]
        for section in card["sections"]
        if section["title"] == "质保与售后"
    )

    response = client.get(
        f"/api/v1/support/cards/product/{card['card_id']}/sections/{section_id}",
        headers=ALICE,
    )

    assert response.status_code == 200
    detail = response.json()
    assert detail["section"]["id"] == section_id
    assert "24 个月" in detail["section"]["content"]
    assert detail["section"]["chars"] == len(detail["section"]["content"])


def test_policy_question_returns_current_version_and_citation(
    client: TestClient,
) -> None:
    body = _message(client, ALICE, "退款条件是什么？")

    assert body["intent"] == "POLICY_QA"
    card = body["context_cards"][0]
    assert card["kind"] == "policy"
    assert card["fields"]["version"] == "v3"
    assert card["sections"]

    section = client.get(
        f"/api/v1/support/cards/policy/{card['card_id']}/sections/{card['sections'][0]['id']}",
        headers=ALICE,
    )
    assert section.status_code == 200
    detail = section.json()
    assert detail["citation"]["version"] == "v3"
    assert detail["citation"]["document_id"] == card["card_id"]
    assert detail["section"]["content"]


def test_order_and_logistics_question_returns_order_card(
    client: TestClient,
) -> None:
    body = _message(client, ALICE, "订单 ORD-DEMO-1002 的物流到哪了？")

    assert body["intent"] == "LOGISTICS_QUERY"
    assert body["task"] is None
    card = body["context_cards"][0]
    assert card["kind"] == "order"
    assert card["fields"]["order_id"] == "ORD-DEMO-1002"
    assert "物流" in body["reply"]
    assert [section["title"] for section in card["sections"]] == [
        "订单字段",
        "物流记录",
        "退款资格",
    ]

    detail = client.get(
        "/api/v1/support/cards/order/ORD-DEMO-1002/sections/section-2",
        headers=ALICE,
    )
    assert detail.status_code == 200
    assert detail.json()["section"]["id"] == "section-2"
    assert "DE10020002" in detail.json()["section"]["content"]


def test_cross_tenant_order_is_not_disclosed(client: TestClient) -> None:
    body = _message(client, CAROL, "查询订单 ORD-DEMO-1001")

    assert body["intent"] == "ORDER_QUERY"
    assert body["context_cards"] == []
    assert "没有找到" in body["reply"]
    assert "alice" not in json.dumps(body, ensure_ascii=False)
    assert "12900" not in json.dumps(body, ensure_ascii=False)

    card = client.get(
        "/api/v1/support/cards/order/ORD-DEMO-1001", headers=CAROL
    )
    assert card.status_code == 404
    assert "12900" not in card.text


def test_cross_tenant_policy_card_is_not_disclosed(client: TestClient) -> None:
    card_id = _message(client, ALICE, "退款条件是什么？")["context_cards"][0][
        "card_id"
    ]

    response = client.get(
        f"/api/v1/support/cards/policy/{card_id}", headers=CAROL
    )

    assert response.status_code == 404


def test_refund_request_still_creates_approval_task(client: TestClient) -> None:
    body = _message(
        client, ALICE, "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题"
    )

    assert body["intent"] == "REFUND_REQUEST"
    assert body["context_cards"] == []
    assert body["task"] is not None
    assert body["task"]["status"] == "WAITING_APPROVAL"
    assert body["task"]["pending_approval"]["amount_cents"] == 12900
    approvals = client.get("/api/v1/approvals", headers=REVIEWER).json()
    assert approvals["total"] == 1
    assert approvals["items"][0]["order_id"] == "ORD-DEMO-1001"


def test_unknown_request_offers_guidance(client: TestClient) -> None:
    body = _message(client, ALICE, "你好呀")

    assert body["intent"] == "UNKNOWN"
    assert body["context_cards"] == []
    assert "商品" in body["reply"]
    assert body["suggestions"]


def test_unknown_card_kind_and_section_are_rejected(client: TestClient) -> None:
    bad_kind = client.get("/api/v1/support/cards/unknown/whatever", headers=ALICE)
    assert bad_kind.status_code == 422
    assert bad_kind.json()["error"]["code"] == "VALIDATION_ERROR"

    missing = client.get(
        "/api/v1/support/cards/product/PROD-AD-1001/sections/not-a-section",
        headers=ALICE,
    )
    assert missing.status_code == 404
