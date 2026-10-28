from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent import actions
from app.agent.intent_catalog import IntentCatalog, _load, get_catalog
from app.agent.routing import _WRITE_INTENT_EXEMPT, _WRITE_INTENT_IDS, classify
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
    # F-9 阶段 B（dispatch C5）：每一行 `write_path: true` 都必须被 routing 的
    # 白名单或豁免集点名。否则新增一行写意图会被 `_match_write_intent` 静默跳过，
    # 「加一行即生效」第二次失效，而且失效时没有任何报警。
    claimable = {member.value for member in _WRITE_INTENT_IDS} | set(_WRITE_INTENT_EXEMPT)
    unclaimed = [
        spec.intent_id
        for spec in catalog.ordered_specs()
        if spec.write_path and spec.intent_id not in claimable
    ]
    assert unclaimed == [], (
        f"未登记的写路径行 {unclaimed}：先扩 Intent 与 routing._WRITE_INTENT_IDS，"
        "或显式加入 routing._WRITE_INTENT_EXEMPT 并写明理由"
    )


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
        "refund_reason",
    )
    assert catalog.missing_slots("REFUND_REQUEST", {"order_id"}) == (
        "amount_cents",
        "refund_reason",
    )
    assert catalog.missing_slots("REFUND_REQUEST", {"order_id", "amount_cents"}) == (
        "refund_reason",
    )
    assert catalog.missing_slots(
        "REFUND_REQUEST", {"order_id", "amount_cents", "refund_reason"}
    ) == ()


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

    # F-9 阶段 B（验收 A3）：判定源在 YAML，不在代码。注入一行 `write_path: true`
    # 的假意图，catalog 立刻读到它；同一句话在生产目录里读不出来。跑完即弃，
    # 不进 intents.yaml（本轮不扩目录，用户已裁定）。
    faked = tmp_path / "intents_fake.yaml"
    faked.write_text(
        _catalog_yaml(
            """
            - intent_id: FAKE_WRITE
              priority: 12
              nature: action
              action: ticket.create
              required_slots: []
              validation: idempotency
              risk_level: R2
              no_evidence: none
              fallback: handoff
              write_path: true
              match_request_only: true
              cues:
                - "假写的动作词"
            """
        ),
        encoding="utf-8",
    )
    fake_hit = _load(faked).match("假写的动作词")
    assert fake_hit is not None and fake_hit.intent_id == "FAKE_WRITE", (
        f"假意图未生效：catalog.match() 返回 "
        f"{fake_hit.intent_id if fake_hit else None}，期望 FAKE_WRITE"
    )
    assert get_catalog().match("假写的动作词") is None

    # 写路径加词同样只动 YAML：给 REFUND_REQUEST 追加一条 cue 后，classify() 立刻
    # 把它读成退款请求。之所以用既有成员演示，是因为 `_match_write_intent` 只认领
    # `_WRITE_INTENT_IDS` 里的行——新写意图必须先扩枚举，那条约束由上面的子集断言看守。
    widened = tmp_path / "intents_widened.yaml"
    widened.write_text(
        _catalog_yaml().replace(
            '      - "refund"\n', '      - "refund"\n      - "那笔钱给我退回来"\n'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.agent.intent_catalog.get_catalog", lambda: _load(widened), raising=True
    )
    assert classify("那笔钱给我退回来", {}).intent is Intent.REFUND_REQUEST
    monkeypatch.undo()
    assert classify("那笔钱给我退回来", {}).intent is Intent.UNKNOWN


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


# --- 退款原因槽位（.qoder/rules/refund-reason-taxonomy.md §5.6.1、§5.6.3）-----


def test_the_refund_reason_slot_offers_the_other_exit() -> None:
    catalog = get_catalog()

    slot = catalog.slot("refund_reason")

    assert slot.name == "refund_reason"
    assert "其他" in slot.prompt
    assert "说不清" in slot.prompt
    assert "其他" in catalog.prompt_for("REFUND_REQUEST", "refund_reason")


def test_refund_request_requires_a_refund_reason_but_never_a_refund_type() -> None:
    catalog = get_catalog()

    spec = catalog.get("REFUND_REQUEST")

    assert "refund_reason" in spec.required_slots
    assert "refund_type" not in spec.required_slots
    assert catalog.missing_slots("REFUND_REQUEST", {"order_id", "amount_cents"}) == (
        "refund_reason",
    )


def test_the_catalog_version_is_bumped_with_the_new_slot() -> None:
    catalog = get_catalog()

    # 版本号写死在这里：目录文件被改动而版本号没动，这条就红。
    # （旧写法是拿目录自己读出来的版本号比目录自己的版本号，恒真。）
    # 2026-10-27：写意图的 cues 与 match_request_only 迁进目录（F-9 阶段 B 步骤 1）。
    assert catalog.version == "2026-10-27"
    assert catalog.version != "2026-10-26"
    assert catalog.version != "2026-10-24"


def test_the_new_slot_needs_no_relaxed_validator(tmp_path: Path) -> None:
    """The slot uses existing ``_SLOT_FIELDS`` keys, so the guard still bites."""
    base = Path(__file__).resolve().parents[1] / "app" / "agent" / "intents.yaml"
    text = base.read_text(encoding="utf-8")
    broken_text = re.sub(
        r"(  refund_reason:\n    type: [^\n]+\n)",
        r"\1    reson: \"typo\"\n",
        text,
        count=1,
    )
    assert broken_text != text, "退款原因槽位未在目录中声明"
    broken = tmp_path / "intents.yaml"
    broken.write_text(broken_text, encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown field"):
        _load(broken)
