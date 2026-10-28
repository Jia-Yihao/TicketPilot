from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.actions import ActionRequest, policy_question
from app.agent.turns import SupportIntent, SupportPlan, Turn
from app.core.security import Identity
from app.observability.tracing import TraceRecorder
from app.retrieval.search import PolicySearchService
from app.services.catalog import CatalogService
from app.services.support import SupportContextService
from tests.conftest import ALICE, CAROL, REVIEWER

DEMO = Identity(tenant_id="demo", user_id="alice", roles=frozenset())
NO_POLICIES = Identity(tenant_id="acme", user_id="carol", roles=frozenset())

LIBRARY_IS_EMPTY = "未检索到当前租户下仍在有效期内的适用政策。建议转人工核实。"


class _NothingFound:
    """A searcher that never matches, whatever the library holds."""

    def search(self, identity, query, *, today=None, limit=None):  # noqa: ANN001, ANN202
        return []


@contextmanager
def _contexts(
    app: FastAPI, *, policy_search: PolicySearchService | _NothingFound | None = None
) -> Iterator[SupportContextService]:
    """A context service on its own session; closes it on the way out."""
    with app.state.session_factory() as session:
        yield SupportContextService(
            session,
            CatalogService(),
            engine=app.state.policy,
            policy_search=policy_search,  # type: ignore[arg-type]
        )


def _action_request(
    app: FastAPI,
    contexts: SupportContextService,
    message: str,
    trace: TraceRecorder,
    *,
    identity: Identity = DEMO,
) -> ActionRequest:
    return ActionRequest(
        session=contexts.session,
        identity=identity,
        contexts=contexts,
        catalog=CatalogService(),
        policy=app.state.policy,
        runtime=app.state.runtime,
        analytics=app.state.analytics,
        plan=SupportPlan(intent=SupportIntent.POLICY_QA),
        state=Turn(conversation_id="conv-empty-policy"),
        message=message,
        request_text=message,
        context={},
        trace=trace,
    )


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


# ------------------------------------------------- why a policy search is empty
def test_a_blank_question_is_named_an_empty_query(app: FastAPI) -> None:
    """Nothing was asked, so nothing was searched for."""
    with _contexts(app) as contexts:
        evidence = contexts.policy_evidence(DEMO, "   ")

    assert evidence.cards == []
    assert evidence.best is None
    assert evidence.empty_reason == "empty_query"


def test_a_tenant_without_a_policy_clause_is_the_library_being_empty(
    app: FastAPI,
) -> None:
    """Tenant ``acme`` seeds no policy document at all: the library is empty,
    not this phrasing."""
    with _contexts(app) as contexts:
        evidence = contexts.policy_evidence(NO_POLICIES, "退款条件是什么？")

    assert evidence.cards == []
    assert evidence.best is None
    assert evidence.empty_reason == "no_visible_chunks"


def test_a_clause_that_exists_but_does_not_match_is_a_miss(app: FastAPI) -> None:
    """The very same tenant, the very same question, only the searcher is
    blind: now the clause is there and this phrasing missed it."""
    with _contexts(app, policy_search=_NothingFound()) as contexts:
        evidence = contexts.policy_evidence(DEMO, "退款条件是什么？")

    assert evidence.cards == []
    assert evidence.best is None
    assert evidence.empty_reason == "no_match"


def test_a_blank_question_never_counts_the_library(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank query is classified without a second trip to the database."""
    def _boom(self: PolicySearchService, identity: Identity, *, today: object = None) -> None:
        raise AssertionError("a blank question must not count the visible chunks")

    monkeypatch.setattr(PolicySearchService, "visible_chunks", _boom)

    with _contexts(app) as contexts:
        evidence = contexts.policy_evidence(DEMO, "")

    assert evidence.empty_reason == "empty_query"


def test_the_two_kinds_of_empty_retrieval_get_different_replies(
    app: FastAPI,
) -> None:
    """The action layer keeps "no such clause" and "not this phrasing" apart.

    Customer-visible text is not where this shows: the ``no_evidence`` signal
    replaces the draft (registered cost, ``.qoder/rules/score-kinds-and-scales.md``
    §2.4). The draft and the trace are.
    """
    with _contexts(app) as contexts:
        library = policy_question(
            _action_request(
                app,
                contexts,
                "退款条件是什么？",
                TraceRecorder(tenant_id="acme"),
                identity=NO_POLICIES,
            )
        )
    with _contexts(app, policy_search=_NothingFound()) as contexts:
        miss = policy_question(
            _action_request(
                app,
                contexts,
                "退款条件是什么？",
                TraceRecorder(tenant_id="demo"),
            )
        )

    assert library.reply == LIBRARY_IS_EMPTY
    assert miss.reply != LIBRARY_IS_EMPTY
    assert "没检索到" in miss.reply
    assert library.evidence is None
    assert miss.evidence is None
    assert library.resolved is False
    assert miss.resolved is False


def test_the_retrieve_span_names_the_empty_reason(app: FastAPI) -> None:
    trace = TraceRecorder(tenant_id="demo")
    with _contexts(app, policy_search=_NothingFound()) as contexts:
        policy_question(
            _action_request(app, contexts, "退款条件是什么？", trace)
        )

    attributes = trace.attributes_of("retrieve")
    assert attributes["hits"] == 0
    assert attributes["empty_reason"] == "no_match"
    assert attributes["evidence_score"] is None
    assert attributes["evidence_kind"] is None


def test_the_retrieve_span_names_the_scale_of_a_real_hit(app: FastAPI) -> None:
    """A hit carries a number *and* the scale it was measured on."""
    trace = TraceRecorder(tenant_id="demo")
    with _contexts(app) as contexts:
        policy_question(
            _action_request(app, contexts, "退款条件是什么？", trace)
        )

    attributes = trace.attributes_of("retrieve")
    assert attributes["hits"] >= 1
    assert attributes["empty_reason"] is None
    assert attributes["evidence_kind"] == "sparse"
    assert isinstance(attributes["evidence_score"], float)


# ------------------------------------------------------- end to end, unchanged
def test_a_statistics_answer_is_not_handed_over(client: TestClient) -> None:
    """The action layer's own 0/1 verdict is not a weak retrieval score, so a
    statistics answer keeps its answer instead of becoming a handoff."""
    body = _message(client, ALICE, "我买了多少订单")

    assert body["intent"] == "SELF_ANALYTICS"
    assert body["context_cards"]
    assert body["handoff"] is None
