"""A statistics answer can be opened, but only by the person who asked.

The first answer carries a ``query_id`` and an aggregate. "哪几笔" then reads
the snapshot's L3 rows and returns a detail card with the same id — no model
call, no new query, and nothing for another identity to open.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.sql.narrative import NarrationCall, NarrationPrompt

ALICE = {"X-Tenant-Id": "demo", "X-User-Id": "alice"}
BOB = {"X-Tenant-Id": "demo", "X-User-Id": "bob"}

CARD_KEYS = {
    "kind",
    "card_id",
    "title",
    "subtitle",
    "summary",
    "badges",
    "fields",
    "sections",
    "source",
    "total_chars",
}


class StubNarrator:
    model_name = "stub"

    def __init__(self) -> None:
        self.prompts: list[NarrationPrompt] = []

    def narrate(self, prompt: NarrationPrompt) -> tuple[str | None, NarrationCall]:
        self.prompts.append(prompt)
        return "你有 2 单订单记录，金额合计 209.00 元。", NarrationCall(requests=1)


def _ask(
    client: TestClient,
    message: str,
    *,
    headers: dict[str, str] = ALICE,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"message": message}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    response = client.post("/api/v1/support/messages", json=body, headers=headers)
    assert response.status_code == 200
    return response.json()


def test_the_statistic_remembers_its_query_id(client: TestClient) -> None:
    first = _ask(client, "我买了多少订单")
    card = first["context_cards"][0]

    assert first["slots"]["last_analytics_query_id"] == card["card_id"]
    assert card["kind"] == "analytics"


def test_a_follow_up_returns_the_same_snapshot(
    app_factory,  # type: ignore[no-untyped-def]
) -> None:
    application: FastAPI = app_factory()
    narrator = StubNarrator()
    application.state.support_agent.narrator = narrator
    with TestClient(application) as client:
        first = _ask(client, "我买了多少订单")
        query_id = first["context_cards"][0]["card_id"]
        assert len(narrator.prompts) == 1

        follow = _ask(
            client,
            "哪几笔",
            conversation_id=first["conversation_id"],
        )

    assert len(narrator.prompts) == 1  # the follow-up never reaches a model
    card = follow["context_cards"][0]
    assert card["kind"] == "analytics_detail"
    assert card["card_id"] == query_id
    assert card["fields"]["records"] == 2
    assert card["fields"]["shown"] == 2
    assert len(card["sections"]) == 2
    assert "明细" in follow["reply"]
    assert follow["slots"]["last_analytics_query_id"] == query_id


def test_the_detail_card_keeps_the_card_shape(client: TestClient) -> None:
    first = _ask(client, "我买了多少订单")
    follow = _ask(client, "请列出明细", conversation_id=first["conversation_id"])
    card = follow["context_cards"][0]

    assert CARD_KEYS <= set(card)
    assert card["kind"] == "analytics_detail"
    assert card["source"].startswith("ledger://")
    assert all({"id", "title", "preview", "chars"} <= set(section) for section in card["sections"])


def test_a_missing_snapshot_asks_for_a_new_question(client: TestClient) -> None:
    first = _ask(client, "我买了多少订单")
    # Simulate the snapshot expiring (or another worker having built it).
    client.app.state.analytics.snapshots.clear()
    follow = _ask(client, "哪几笔", conversation_id=first["conversation_id"])

    kinds = [card["kind"] for card in follow["context_cards"]]
    assert "analytics_detail" not in kinds
    assert "重新" in follow["reply"]
    assert follow["slots"]["last_analytics_query_id"] is None


def test_another_customer_cannot_open_the_details(client: TestClient) -> None:
    first = _ask(client, "我买了多少订单")
    follow = _ask(
        client,
        "哪几笔",
        headers=BOB,
        conversation_id=first["conversation_id"],
    )

    assert follow["conversation_id"] != first["conversation_id"]
    assert all(card["kind"] != "analytics_detail" for card in follow["context_cards"])


def test_a_sql_answer_leaves_nothing_to_open(client: TestClient) -> None:
    grouped = _ask(client, "按状态统计我的订单笔数")
    assert grouped["context_cards"][0]["kind"] == "analytics"
    assert grouped["slots"]["last_analytics_query_id"] is None

    follow = _ask(client, "哪几笔", conversation_id=grouped["conversation_id"])
    assert all(card["kind"] != "analytics_detail" for card in follow["context_cards"])


def test_a_new_statistic_replaces_the_open_one(client: TestClient) -> None:
    first = _ask(client, "我买了多少订单")
    second = _ask(
        client,
        "我退了几笔款",
        conversation_id=first["conversation_id"],
    )
    assert second["slots"]["last_analytics_query_id"] == second["context_cards"][0]["card_id"]
    assert second["slots"]["last_analytics_query_id"] != first["context_cards"][0]["card_id"]


def test_a_policy_question_does_not_open_the_snapshot(client: TestClient) -> None:
    first = _ask(client, "我买了多少订单")
    follow = _ask(
        client,
        "售后政策明细有哪些",
        conversation_id=first["conversation_id"],
    )

    kinds = [card["kind"] for card in follow["context_cards"]]
    assert "analytics_detail" not in kinds
    assert follow["context_cards"]
    assert follow["context_cards"][0]["kind"] == "policy"


def test_a_sql_statistic_closes_the_previous_snapshot(client: TestClient) -> None:
    first = _ask(client, "我买了多少订单")
    assert first["slots"]["last_analytics_query_id"] is not None

    grouped = _ask(
        client,
        "按状态统计我的订单笔数",
        conversation_id=first["conversation_id"],
    )
    assert grouped["slots"]["last_analytics_query_id"] is None

    follow = _ask(client, "哪几笔", conversation_id=first["conversation_id"])
    assert all(card["kind"] != "analytics_detail" for card in follow["context_cards"])
