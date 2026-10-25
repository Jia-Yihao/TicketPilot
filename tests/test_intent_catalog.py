from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent import actions
from app.agent.intent_catalog import IntentCatalog, _load, get_catalog
from app.agent.support_agent import SupportIntent
from app.agent.turns import SupportPlan
from app.models.enums import Intent
from tests.conftest import ALICE


def _message(client: TestClient, text: str) -> dict:
    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": text},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _catalog_yaml(extra_intent: str = "") -> str:
    base = Path(__file__).resolve().parents[1] / "app" / "agent" / "intents.yaml"
    text = base.read_text(encoding="utf-8")
    if not extra_intent:
        return text
    block = textwrap.dedent(extra_intent).strip("\n")
    indented = "\n".join(f"  {line}" if line else line for line in block.splitlines())
    return f"{text}\n{indented}\n"


def test_the_catalog_loads_and_declares_its_version() -> None:
    catalog = get_catalog()

    assert catalog.version
    assert catalog.specs


def test_every_enum_label_has_a_catalog_row() -> None:
    catalog = get_catalog()

    for intent in Intent:
        assert catalog.has(intent.value), intent.value
    for intent in SupportIntent:
        assert catalog.has(intent.value), intent.value


def test_every_intent_binds_an_action_that_exists() -> None:
    catalog = get_catalog()

    for spec in catalog.ordered_specs():
        assert spec.action, spec.intent_id
        assert spec.action[0] in actions.REGISTRY, (spec.intent_id, spec.action[0])


def test_required_slots_resolve_to_a_known_slot_and_a_prompt() -> None:
    catalog = get_catalog()

    for spec in catalog.ordered_specs():
        for slot in spec.required_slots:
            assert catalog.slot(slot).prompt
            assert catalog.prompt_for(spec.intent_id, slot)


def test_missing_slots_follow_the_catalog_order() -> None:
    catalog = get_catalog()

    assert catalog.missing_slots("REFUND_REQUEST", set()) == (
        "order_id",
        "amount_cents",
    )
    assert catalog.missing_slots("REFUND_REQUEST", {"order_id"}) == ("amount_cents",)
    assert catalog.missing_slots("REFUND_REQUEST", {"order_id", "amount_cents"}) == ()


def test_a_row_that_requires_an_unknown_slot_is_rejected(tmp_path: Path) -> None:
    broken = tmp_path / "intents.yaml"
    broken.write_text(
        _catalog_yaml(
            """
            - intent_id: BROKEN_INTENT
              priority: 5
              nature: info
              action: order.get
              required_slots:
                - not_a_slot
              validation: none
              risk_level: R0
              no_evidence: none
              fallback: clarify
              write_path: false
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown slot"):
        _load(broken)


def test_a_duplicate_intent_row_is_rejected(tmp_path: Path) -> None:
    broken = tmp_path / "intents.yaml"
    broken.write_text(
        _catalog_yaml(
            """
            - intent_id: ORDER_QUERY
              priority: 5
              nature: info
              action: order.get
              required_slots: []
              validation: none
              risk_level: R0
              no_evidence: none
              fallback: clarify
              write_path: false
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate intent"):
        _load(broken)


def test_a_misspelled_field_is_rejected(tmp_path: Path) -> None:
    """A typo in the catalog must not silently fall back to the default."""
    broken = tmp_path / "intents.yaml"
    broken.write_text(
        _catalog_yaml(
            """
            - intent_id: TYPO_INTENT
              priority: 5
              nature: info
              action: order.get
              required_slots: []
              validation: none
              risk_level: R0
              no_evidence: none
              fallback: clarify
              write_path: false
              write_pth: true
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown field"):
        _load(broken)


def test_a_misspelled_slot_field_is_rejected(tmp_path: Path) -> None:
    base = Path(__file__).resolve().parents[1] / "app" / "agent" / "intents.yaml"
    text = base.read_text(encoding="utf-8").replace(
        "    sensitive: false\n", "    sensitive: false\n    sensative: true\n", 1
    )
    broken = tmp_path / "intents.yaml"
    broken.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown field"):
        _load(broken)


def test_the_cues_live_in_the_catalog_not_in_the_agent() -> None:
    catalog = get_catalog()

    spec = catalog.get("REFUND_STATUS")
    assert "退款没到账" in spec.cues
    assert catalog.get("UNKNOWN").cues == ()


def test_the_catalog_routes_a_message_to_its_row() -> None:
    catalog = get_catalog()

    spec = catalog.match("我的退款还没到账")

    assert spec is not None
    assert spec.intent_id == "REFUND_STATUS"


def test_a_new_row_becomes_a_usable_intent_without_agent_code(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The acceptance criterion of the catalog: a new capability is a YAML row.

    The row below adds a warranty-lookup intent that is served by the
    already registered ``catalog.search`` action. Nothing in
    ``support_agent.py`` changes.
    """
    extended = tmp_path / "intents.yaml"
    extended.write_text(
        _catalog_yaml(
            """
            - intent_id: WARRANTY_LOOKUP
              priority: 15
              nature: info
              action: catalog.search
              required_slots: []
              validation: none
              risk_level: R0
              no_evidence: list_candidates
              fallback: handoff
              write_path: false
              cues:
                - "保修政策"
                - "质保范围"
            """
        ),
        encoding="utf-8",
    )
    patched = _load(extended)
    monkeypatch.setattr(
        "app.agent.support_agent.get_catalog", lambda: patched, raising=True
    )

    body = _message(client, "保修政策是怎么规定的")

    assert body["intent"] == "WARRANTY_LOOKUP"
    assert body["clarification"] is None
    assert body["context_cards"]


def test_the_catalog_row_replaces_the_agent_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dropping a cue in the catalog changes the routing, not the code."""
    trimmed = tmp_path / "intents.yaml"
    trimmed.write_text(
        _catalog_yaml().replace('      - "退款还没到账"\n', ""),
        encoding="utf-8",
    )
    patched = _load(trimmed)

    assert patched.match("我的退款还没到账") is None


def test_an_unknown_label_from_the_catalog_is_accepted() -> None:
    label = SupportIntent("SOME_FUTURE_INTENT")

    assert label.value == "SOME_FUTURE_INTENT"
    assert label is SupportIntent("SOME_FUTURE_INTENT")


def test_the_plan_carries_the_catalog_label() -> None:
    plan = SupportPlan(SupportIntent.POLICY_QA, "ORD-DEMO-1001")

    assert plan.intent.value == "POLICY_QA"
    assert plan.order_id == "ORD-DEMO-1001"


def test_the_catalog_is_a_single_source_for_the_action_binding() -> None:
    catalog = get_catalog()

    assert catalog.actions_of("ORDER_QUERY") == ("order.get",)
    assert catalog.actions_of("REFUND_REQUEST") == ("refund.request",)
    assert catalog.actions_of("TICKET_CREATE") == ("ticket.create",)
    assert catalog.actions_of("UNKNOWN") == ("none",)


def test_the_catalog_declares_the_risk_level_of_each_intent() -> None:
    catalog = get_catalog()

    assert catalog.risk_of("ORDER_QUERY").value == "R0"
    assert catalog.risk_of("REFUND_REQUEST").value == "R3"
    assert catalog.risk_of("TICKET_CREATE").value == "R2"


def test_order_scoped_intents_are_the_ones_that_need_an_order_id() -> None:
    catalog = get_catalog()

    scoped = set(catalog.order_scoped_ids())

    assert "ORDER_QUERY" in scoped
    assert "REFUND_REQUEST" in scoped
    assert "POLICY_QA" not in scoped
    assert "TICKET_CREATE" not in scoped


def test_the_catalog_object_is_a_typed_view(tmp_path: Path) -> None:
    catalog: IntentCatalog = get_catalog()

    assert isinstance(catalog, IntentCatalog)
    assert catalog.get("ORDER_QUERY").requires("order_id")
    assert not catalog.get("ORDER_QUERY").requires("amount_cents")
