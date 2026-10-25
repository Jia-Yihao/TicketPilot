"""G6: what customer memory is allowed to do to an answer.

These cases exist to hold two claims that ADR-014 used to make the other way
around: that the answer path can read the profile, and that it still cannot
write it, echo it, or make a customer's free text part of a reply.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent.personalization import (
    ADDRESSED,
    CARDS_REORDERED,
    INTEREST_SUGGESTION,
    SUGGESTIONS_TRIMMED,
    ProfileSnapshot,
    personalize,
)
from app.agent.turns import Draft, SupportIntent
from app.models.audit import AuditEvent
from app.models.memory import CustomerProfile
from app.models.observability import TraceSpan
from app.services.catalog import CatalogService
from tests.conftest import ALICE

POLICY_QUESTION = "退款条件是什么？"
TRAILLITE = "TrailLite 通勤背包 22L"
TRAILLITE_PRO = "TrailLite Pro 摄影背包 28L"


def _ask(
    client: TestClient, message: str = POLICY_QUESTION, **context: Any
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/support/messages", headers=ALICE, json={"message": message, **context}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _write_profile(client: TestClient, **changes: Any) -> None:
    assert client.put("/api/v1/memory", headers=ALICE, json=changes).status_code == 200


def _span_names(client: TestClient, app: FastAPI, trace_id: str) -> list[str]:
    with app.state.session_factory() as session:
        return [
            span.name
            for span in session.scalars(
                select(TraceSpan)
                .where(TraceSpan.trace_id == trace_id)
                .order_by(TraceSpan.started_at, TraceSpan.id)
            ).all()
        ]


def _profile_row(app: FastAPI) -> CustomerProfile:
    with app.state.session_factory() as session:
        row = session.scalars(
            select(CustomerProfile).where(
                CustomerProfile.tenant_id == "demo",
                CustomerProfile.user_id == "alice",
            )
        ).one()
        session.refresh(row)
        return row


def _memory_actions(app: FastAPI) -> list[str]:
    with app.state.session_factory() as session:
        return [
            str(action)
            for action in session.scalars(select(AuditEvent.action).where(
                AuditEvent.action.like("memory.%")
            )).all()
        ]


# ------------------------------------------------------------------- absence


def test_a_customer_without_a_profile_is_not_personalized(client: TestClient) -> None:
    body = _ask(client)
    assert body["personalization"] is None
    assert not body["reply"].startswith("你好，")


def test_the_answer_path_creates_no_profile(client: TestClient, app: FastAPI) -> None:
    _ask(client)
    assert client.get("/api/v1/memory", headers=ALICE).json()["preferred_name"] is None
    # Reading the profile in the answer path must not create the row either.
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(CustomerProfile.id))) == 0


# ------------------------------------------------------------------- greeting


def test_the_name_is_used_once_when_the_conversation_opens(
    client: TestClient, app: FastAPI
) -> None:
    _write_profile(client, preferred_name="Alice 女士")

    first = _ask(client)
    assert first["personalization"] == {
        "effects": [ADDRESSED],
        "fields": ["preferred_name"],
    }
    assert first["reply"].startswith("你好，Alice 女士。")

    second = _ask(client, conversation_id=first["conversation_id"])
    assert second["personalization"] is None or ADDRESSED not in second[
        "personalization"]["effects"]
    assert not second["reply"].startswith("你好，")


def test_the_greeting_is_recorded_in_the_trace_before_the_answer_span(
    client: TestClient, app: FastAPI
) -> None:
    _write_profile(client, preferred_name="Alice 女士")
    body = _ask(client)
    names = _span_names(client, app, str(body["trace_id"]))
    assert "memory.personalize" in names
    assert names.index("memory.personalize") < names.index("support.reply")


# ------------------------------------------------------------- no free-text echo


def test_history_and_contact_window_never_reach_the_answer(
    client: TestClient,
) -> None:
    _write_profile(
        client,
        preferred_name="Alice 女士",
        history_summary="周五下午三点前回复我，找王经理",
        preferences={"contact_window": "周五下午三点"},
    )
    body = _ask(client)
    for fragment in ("周五", "下午三点", "王经理"):
        assert fragment not in body["reply"]
        assert all(fragment not in item for item in body["suggestions"])
    # Only the two fields with an allowed effect are ever named back.
    assert body["personalization"]["fields"] == ["preferred_name"]


def test_an_interest_the_catalog_does_not_recognise_is_inert(
    client: TestClient,
) -> None:
    _write_profile(
        client,
        preferences={"product_interests": ["<script>alert(1)</script>", "随便一句话"]},
    )
    body = _ask(client)
    assert body["personalization"] is None
    assert all("<script>" not in item for item in body["suggestions"])
    assert "<script>" not in body["reply"]


# ------------------------------------------------------------------- interests


def test_a_catalog_interest_becomes_one_suggestion_with_the_catalog_name(
    client: TestClient,
) -> None:
    _write_profile(client, preferences={"product_interests": [TRAILLITE, TRAILLITE]})
    body = _ask(client)
    assert INTEREST_SUGGESTION in body["personalization"]["effects"]
    assert body["suggestions"][0] == f"{TRAILLITE} 的质保范围"
    # Deduped by product, so the same interest cannot stack two chips.
    assert sum(TRAILLITE in item for item in body["suggestions"]) == 1


def test_an_interest_about_the_product_already_answered_adds_nothing(
    client: TestClient,
) -> None:
    _write_profile(client, preferences={"product_interests": ["SKU-AD-1001"]})
    body = _ask(client, message="SKU-AD-1001 的参数是什么")
    assert body["intent"] == "PRODUCT_QUERY"
    assert body["personalization"] is None


def test_concise_tone_trims_suggestions_without_touching_the_answer(
    client: TestClient,
) -> None:
    before = _ask(client)
    assert len(before["suggestions"]) > 2
    _write_profile(client, preferences={"tone": "concise"})
    body = _ask(client)
    assert body["personalization"] == {
        "effects": [SUGGESTIONS_TRIMMED],
        "fields": ["tone"],
    }
    assert body["suggestions"] == before["suggestions"][:2]
    assert body["reply"] == before["reply"]


# ------------------------------------------------------------------ reordering


def test_a_matching_interest_moves_its_own_card_first() -> None:
    catalog = CatalogService()
    products = catalog.list_products()
    first, second = products[0], products[1]
    draft = Draft(
        intent=SupportIntent.PRODUCT_QUERY,
        reply="找到 2 款相关商品。",
        cards=[
            {"kind": "product", "card_id": first.product_id},
            {"kind": "product", "card_id": second.product_id},
        ],
    )
    result = personalize(
        draft,
        ProfileSnapshot(
            preferred_name=None, tone=None, interests=(second.name,)
        ),
        catalog,
        first_turn=False,
        product_id=None,
    )
    assert result.effects == [CARDS_REORDERED]
    assert result.matched_product_id == second.product_id
    assert [card["card_id"] for card in draft.cards] == [
        second.product_id,
        first.product_id,
    ]
    assert draft.reply == "找到 2 款相关商品。"


def test_a_card_that_is_already_first_is_not_counted_as_reordered() -> None:
    catalog = CatalogService()
    product = catalog.list_products()[0]
    draft = Draft(
        intent=SupportIntent.PRODUCT_QUERY,
        reply="答案",
        cards=[{"kind": "product", "card_id": product.product_id}],
    )
    result = personalize(
        draft,
        ProfileSnapshot(None, None, (product.name,)),
        catalog,
        first_turn=False,
        product_id=None,
    )
    assert result.effects == []
    assert result.to_dict() == {}


def test_a_clarification_turn_is_personalized_in_the_reply_only(
    client: TestClient,
) -> None:
    _write_profile(
        client,
        preferred_name="Alice 女士",
        preferences={"tone": "concise", "product_interests": [TRAILLITE]},
    )
    body = _ask(client, message="我要申请退款")
    assert body["clarification"] is not None
    assert body["clarification"]["reason"] == "MISSING_ORDER_ID"
    assert body["reply"].startswith("你好，Alice 女士。")
    # The structured clarification the client resumes from stays the template.
    assert not body["clarification"]["prompt"].startswith("你好，")
    assert len(body["suggestions"]) == 2


def test_an_interest_moves_its_card_first_in_a_two_card_answer(
    client: TestClient,
) -> None:
    _write_profile(client, preferences={"product_interests": [TRAILLITE_PRO]})
    body = _ask(client, message="TrailLite 的参数是什么？")
    assert body["intent"] == "PRODUCT_QUERY"
    assert [card["card_id"] for card in body["context_cards"]] == [
        "PROD-TL-2003",
        "PROD-TL-2002",
    ]
    effects = body["personalization"]["effects"]
    assert CARDS_REORDERED in effects
    # Both products are already on screen, so no interest chip is stacked on top.
    assert INTEREST_SUGGESTION not in effects


def test_an_interest_already_on_the_first_card_leaves_the_answer_alone(
    client: TestClient,
) -> None:
    _write_profile(client, preferences={"product_interests": [TRAILLITE]})
    body = _ask(client, message="TrailLite 的参数是什么？")
    assert [card["card_id"] for card in body["context_cards"]] == [
        "PROD-TL-2002",
        "PROD-TL-2003",
    ]
    assert body["personalization"] is None


# ------------------------------------------------------------------ writes, off


def test_personalizing_writes_no_profile_row_and_no_memory_audit(
    client: TestClient, app: FastAPI
) -> None:
    _write_profile(client, preferred_name="Alice 女士", locale="zh-CN")
    before = _profile_row(app).updated_at
    _memory_actions(app)  # the PUT above audited one memory.update; nothing after
    _ask(client)
    _ask(client)

    after = _profile_row(app)
    assert after.updated_at == before
    assert after.preferred_name == "Alice 女士"
    assert _memory_actions(app) == ["memory.update"]


def test_the_switch_restores_the_plain_answer(app_factory: Callable[..., FastAPI]) -> None:
    on_client = TestClient(app_factory())
    _write_profile(on_client, preferred_name="Alice 女士", locale="zh-CN")
    personalized = _ask(on_client)

    off_app = app_factory(memory_personalization_enabled=False)
    off_client = TestClient(off_app)
    _write_profile(off_client, preferred_name="Alice 女士", locale="zh-CN")
    plain = _ask(off_client)

    assert personalized["personalization"] is not None
    assert plain["personalization"] is None
    assert plain["reply"] == personalized["reply"][len("你好，Alice 女士。") :]
    assert plain["suggestions"] == personalized["suggestions"]


def test_deleting_the_profile_reverts_the_answer(client: TestClient) -> None:
    _write_profile(client, preferred_name="Alice 女士")
    assert _ask(client)["reply"].startswith("你好，Alice 女士。")
    assert client.delete("/api/v1/memory", headers=ALICE).status_code == 204
    body = _ask(client)
    assert body["personalization"] is None
    assert not body["reply"].startswith("你好，")


# ------------------------------------------------------------------ handoff silence


def test_a_silent_turn_under_takeover_is_not_personalized(client: TestClient) -> None:
    _write_profile(client, preferred_name="Alice 女士")
    first = _ask(client, "转人工")
    assert first["personalization"] is not None
    assert first["reply"].startswith("你好，Alice 女士。")

    second = _ask(client, "订单 ORD-DEMO-1001 到哪了", conversation_id=first["conversation_id"])

    assert second["agent_silent"] is True
    assert second["reply"] == ""
    assert second["personalization"] is None
