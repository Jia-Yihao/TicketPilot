"""R10 T6-b 钉桩：渠道意图直取的完整契约。

口径 ``.qoder/rules/question-gate-coverage-and-channel-intent.md`` §4.1-§4.9，
逐条对应 §5 DoD 第 7-13 条：

7. 白名单是目录推导的精确八值，抄本随目录自动变化，不一致时拒绝放行；
8. ``resolve_declared_intent`` 白名单先于枚举构造（垃圾串不进 ``_value2member_map_``）；
9. 落点只在 ``classify_support`` 的 ``spec is None`` 且规则也失明的一个分支，
   collaboration / 写意图 / 规则命中都不被覆盖；``_silent`` 与 ``accept_web`` 不变；
10. 三处适配器构造点只在 ``isinstance(value, str)`` 时填 ``declared_intent``，
    ``envelope_digest`` 逐字节不变（字面量取自改动前实测）；
11. 零留痕：声明值不以任何专属键出现在 payload / task.context / span / 审计里，
    R7 四计数键不落盘，无 ``ModelDecision``，``classify()`` 不读 ``context["intent"]``；
12. 红线用例在 ``tests/test_llm_gap.py``，本文件不重复其断言，只补第 7-11 条；
13. F-1 交互代价（直取轮不形成反问）与 ``HUMAN_HANDOFF`` 直取只产既有转人工形态。

模型不出现：沿用 ``tests/test_llm_gap`` 的 ``FixedBrain`` 替身与自造爆炸桩。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import app.agent.support_agent as support_agent_module
from app.agent.intent_catalog import IntentCatalog, IntentSpec, get_catalog
from app.agent.routing import RoutePlan, classify
from app.agent.support_agent import (
    SupportIntent,
    SupportPlan,
    classify_support,
    model_gate,
    resolve_declared_intent,
)
from app.channels.base import InboundRequest, MessageEnvelope, envelope_digest
from app.core.config import Settings
from app.main import create_app
from app.models.audit import AuditEvent
from app.models.channel_message import ChannelMessage
from app.models.enums import Channel, Intent, RiskLevel
from app.models.observability import TraceSpan
from app.models.task import Task
from app.seed import seed_all
from tests.conftest import ALICE
from tests.test_channels import (
    _channels_app,
    _im_payload,
    _post_webhook,
    _signed,
)

# §4.3 定死的八个 id，目录序。
EXPECTED_WHITELIST = (
    "HUMAN_HANDOFF",
    "SELF_ANALYTICS",
    "REFUND_STATUS",
    "REFUND_ELIGIBILITY",
    "POLICY_QA",
    "LOGISTICS_QUERY",
    "PRODUCT_QUERY",
    "ORDER_QUERY",
)

# 规则表与意图目录双双失明的句子（tests/test_counter_question_turn.py 同款）。
BLIND_TEXT = "运费谁出？"

# 改动前（工作树实测）三处适配器 normalize 的 envelope_digest 字面量。
# 载体字段不进 material，所以改动后必须逐字节相等——这就是 DoD 10 的证据。
PRE_CHANGE_DIGESTS = {
    "im": "2fe4c64d2ce8b19d2bfd11e6dbadced6291dd46f9aa660f124ddfd9fd36b2bb0",
    "email": "70e60287f291e5df425e911d3629342cc1256b92048d20e15a1f6342d7d1cb82",
    "web": "449ec8bb15bf25439c4239400e632d30147068a2959229bd8810a0eae1d84c3e",
}

IDENTITY_JSON = {"tenant_id": "demo", "user_id": "alice"}


# ------------------------------------------------------------------ helpers
def _web_app(tmp_path: Path) -> FastAPI:
    def factory(**overrides: object) -> FastAPI:
        values: dict[str, object] = {
            "environment": "test",
            "database_url": f"sqlite+pysqlite:///{tmp_path / 'ci.db'}",
            "auto_create_tables": True,
            "mock_refund_mode": "success",
        }
        values.update(overrides)
        return create_app(Settings(**values))

    return _channels_app(factory)


def _channels_client(tmp_path: Path) -> tuple[FastAPI, TestClient]:
    app = _web_app(tmp_path)
    seed_all(app.state.session_factory)
    return app, TestClient(app)


def _im_envelope(app: FastAPI, payload: dict[str, Any]) -> MessageEnvelope:
    adapter = app.state.channel_gateway.adapters[Channel.WECOM]
    return adapter.normalize(
        InboundRequest(
            headers={},
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
    )


def _email_envelope(app: FastAPI, payload: dict[str, Any]) -> MessageEnvelope:
    adapter = app.state.channel_gateway.adapters[Channel.EMAIL]
    return adapter.normalize(
        InboundRequest(
            headers={},
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
    )


def _web_envelope(app: FastAPI, payload: dict[str, Any]) -> MessageEnvelope:
    from app.core.security import Identity

    adapter = app.state.channel_gateway.adapters[Channel.WEB]
    return adapter.normalize(
        InboundRequest(
            headers={},
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            identity=Identity(
                tenant_id="demo", user_id="alice", roles=frozenset()
            ),
        )
    )


def _walk_pairs(value: Any, path: str = "") -> Iterator[tuple[str, str, Any]]:
    """Yield (path, key, leaf_value) for every mapping entry in a JSON shape."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                yield from _walk_pairs(item, f"{path}.{key}")
            else:
                yield path, str(key), item
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, (dict, list)):
                yield from _walk_pairs(item, f"{path}[{index}]")


def _spec_copy(spec: IntentSpec, **overrides: Any) -> IntentSpec:
    values = {name: getattr(spec, name) for name in spec.__dataclass_fields__}
    values.update(overrides)
    return IntentSpec(**values)


def _synthetic_catalog(
    *,
    extra_non_write: tuple[str, ...] = (),
    flip_to_non_write: str | None = None,
    flip_to_write: str | None = None,
) -> IntentCatalog:
    specs = {}
    for spec in get_catalog().specs.values():
        if spec.intent_id == flip_to_non_write:
            spec = _spec_copy(spec, write_path=False)
        elif spec.intent_id == flip_to_write:
            spec = _spec_copy(spec, write_path=True)
        specs[spec.intent_id] = spec
    for name in extra_non_write:
        specs[name] = IntentSpec(
            intent_id=name,
            nature="info",
            action=(),
            required_slots=(),
            validation="none",
            risk_level=RiskLevel.R0,
            no_evidence="none",
            fallback="none",
            write_path=False,
            priority=999,
        )
    return IntentCatalog(version="synthetic", specs=specs, slots={})


def _spec_copy(spec: IntentSpec, **overrides: Any) -> IntentSpec:
    values = {name: getattr(spec, name) for name in spec.__dataclass_fields__}
    values.update(overrides)
    return IntentSpec(**values)


def _route_for(text: str) -> RoutePlan:
    return classify(text, {})


# ------------------------------------------------------- DoD 7：白名单（T6-4）
def test_whitelist_is_exactly_the_derived_eight() -> None:
    assert get_catalog().non_write_declared_intents() == EXPECTED_WHITELIST


def test_whitelist_equals_set_recomputed_from_ordered_specs() -> None:
    recomputed = tuple(
        spec.intent_id
        for spec in get_catalog().ordered_specs()
        if not spec.write_path and spec.intent_id != "UNKNOWN"
    )
    assert get_catalog().non_write_declared_intents() == recomputed


def test_write_path_rows_are_excluded_including_delivery_issue() -> None:
    whitelist = set(get_catalog().non_write_declared_intents())
    for intent_id in ("REFUND_REQUEST", "TICKET_CREATE", "DELIVERY_ISSUE"):
        assert get_catalog().get(intent_id).write_path is True
        assert intent_id not in whitelist


def test_whitelist_follows_catalog_rows_automatically() -> None:
    added = _synthetic_catalog(extra_non_write=("NEW_DECLARE_ROW",))
    assert added.non_write_declared_intents() == EXPECTED_WHITELIST + (
        "NEW_DECLARE_ROW",
    )
    flipped = _synthetic_catalog(flip_to_non_write="REFUND_REQUEST")
    assert set(flipped.non_write_declared_intents()) == set(EXPECTED_WHITELIST) | {
        "REFUND_REQUEST"
    }


# ------------------------------------------------------- DoD 8：解析器（T6-5）
def test_resolver_accepts_every_whitelist_member() -> None:
    for intent_id in get_catalog().non_write_declared_intents():
        reading = resolve_declared_intent(intent_id)
        assert reading is not None and reading.value == intent_id


def test_resolver_rejects_everything_off_the_whitelist() -> None:
    rejected: list[Any] = [
        None,
        7,
        7.5,
        True,
        ["POLICY_QA"],
        {"intent": "POLICY_QA"},
        "",
        "   ",
        "policy_qa",
        "Order_Query",
        "NOT_AN_INTENT",
        "REFUND_REQUEST",
        "TICKET_CREATE",
        "DELIVERY_ISSUE",
        "UNKNOWN",
        " REFUND_STATUS",
    ]
    for value in rejected:
        assert resolve_declared_intent(value) is None, value


def test_resolver_never_registers_junk_in_the_enum() -> None:
    before = set(SupportIntent._value2member_map_)
    junk = (
        "NOT_AN_INTENT",
        "policy_qa",
        "REFUND_STATUS ",
        "HUMAN handoff",
        "x" * 300,
    )
    for value in junk:
        assert resolve_declared_intent(value) is None, value
    # 白名单校验先于 ``SupportIntent(...)``：垃圾串一个都不许进枚举（§4.4）。
    assert set(SupportIntent._value2member_map_) == before


def test_resolver_is_fail_closed_against_a_stale_literal() -> None:
    """解析器只放行目录**当前**推导出的值：行一变成写行，放行面立刻关闭。

    若实现里藏了一份字面抄本，翻转目录后旧 id 会继续被放行——这条用例就是
    防这一手的（§4.3「与目录推导结果不一致时拒绝放行任何值」）。
    """
    original = support_agent_module.get_catalog
    flipped = _synthetic_catalog(flip_to_write="PRODUCT_QUERY")
    try:
        support_agent_module.get_catalog = lambda: flipped
        assert resolve_declared_intent("PRODUCT_QUERY") is None
        assert resolve_declared_intent("REFUND_REQUEST") is None
        assert resolve_declared_intent("POLICY_QA") is not None
    finally:
        support_agent_module.get_catalog = original
    assert resolve_declared_intent("PRODUCT_QUERY") is not None


def test_blind_declared_member_goes_through_missing_not_preconstruction() -> None:
    # HUMAN_HANDOFF / SELF_ANALYTICS 不是声明成员（事实 A）：与规则路径同源，
    # 白名单不等于声明成员集。
    original_members = {
        "PRODUCT_QUERY", "POLICY_QA", "ORDER_QUERY", "LOGISTICS_QUERY",
        "REFUND_ELIGIBILITY", "REFUND_STATUS", "DELIVERY_ISSUE",
        "REFUND_REQUEST", "TICKET_CREATE", "UNKNOWN",
    }
    assert "HUMAN_HANDOFF" not in original_members
    assert "SELF_ANALYTICS" not in original_members
    assert resolve_declared_intent("HUMAN_HANDOFF") is not None


# ------------------------------------------------------ DoD 9：落点（T6-6）
def test_landing_fills_only_the_double_blind_hole() -> None:
    plan = classify_support(
        BLIND_TEXT,
        _route_for(BLIND_TEXT),
        has_catalog_match=False,
        declared_intent="POLICY_QA",
    )
    assert plan.intent is SupportIntent.POLICY_QA


def test_landing_without_declaration_is_verbatim_today() -> None:
    baseline = classify_support(
        BLIND_TEXT, _route_for(BLIND_TEXT), has_catalog_match=False
    )
    for declared in (None, "NOT_AN_INTENT", "policy_qa", 7):
        plan = classify_support(
            BLIND_TEXT,
            _route_for(BLIND_TEXT),
            has_catalog_match=False,
            declared_intent=declared,
        )
        assert plan == baseline
        assert plan.intent is SupportIntent.UNKNOWN


def test_landing_requires_rule_blindness_too() -> None:
    # spec 为 None 但规则表读出了非 UNKNOWN：直取不得填这个洞（§4.5 附加条件）。
    route = RoutePlan(intent=Intent.POLICY_QA)
    plan = classify_support(
        BLIND_TEXT, route, has_catalog_match=False, declared_intent="ORDER_QUERY"
    )
    assert plan.intent is SupportIntent.UNKNOWN


def test_landing_never_overrides_collaboration_short_circuit() -> None:
    text = "转人工"
    route = _route_for(text)
    spec = get_catalog().match(text.lower(), has_catalog_match=False)
    assert spec is not None and spec.nature == "collaboration"
    plan = classify_support(
        text, route, has_catalog_match=False, declared_intent="ORDER_QUERY"
    )
    assert plan.intent.value == "HUMAN_HANDOFF"


def test_landing_never_overrides_the_write_intent_branch() -> None:
    text = "我要申请退款，订单 ORD-DEMO-1001，金额 129 元"
    route = _route_for(text)
    assert route.intent is Intent.REFUND_REQUEST
    plan = classify_support(
        text, route, has_catalog_match=False, declared_intent="POLICY_QA"
    )
    assert plan.intent is SupportIntent.REFUND_REQUEST


def test_landing_never_overrides_a_catalog_hit() -> None:
    text = "查一下物流"
    route = _route_for(text)
    plan = classify_support(
        text, route, has_catalog_match=False, declared_intent="PRODUCT_QUERY"
    )
    assert plan.intent is SupportIntent.LOGISTICS_QUERY


def test_declared_write_intent_never_lands() -> None:
    for text, declared in (
        (BLIND_TEXT, "REFUND_REQUEST"),
        (BLIND_TEXT, "TICKET_CREATE"),
        (BLIND_TEXT, "DELIVERY_ISSUE"),
    ):
        plan = classify_support(
            text, _route_for(text), has_catalog_match=False, declared_intent=declared
        )
        assert plan.intent is SupportIntent.UNKNOWN, declared


# --------------------------------------------------- DoD 10：载体与适配器
def test_im_adapter_carries_a_string_intent(tmp_path: Path) -> None:
    app, _ = _channels_client(tmp_path)
    envelope = _im_envelope(
        app, _im_payload("fixed-1", BLIND_TEXT, intent="POLICY_QA")
    )
    assert envelope.declared_intent == "POLICY_QA"


def test_email_adapter_carries_a_string_intent(tmp_path: Path) -> None:
    app, _ = _channels_client(tmp_path)
    envelope = _email_envelope(
        app,
        {
            "message_id": "fixed-2",
            "from": "alice@example.com",
            "text": BLIND_TEXT,
            "intent": "ORDER_QUERY",
        },
    )
    assert envelope.declared_intent == "ORDER_QUERY"


def test_web_adapter_carries_a_string_intent(tmp_path: Path) -> None:
    app, _ = _channels_client(tmp_path)
    envelope = _web_envelope(
        app, {"message": BLIND_TEXT, "intent": "PRODUCT_QUERY"}
    )
    assert envelope.declared_intent == "PRODUCT_QUERY"


def test_adapters_never_coerce_a_non_string_intent(tmp_path: Path) -> None:
    app, _ = _channels_client(tmp_path)
    cases: list[Any] = [7, 7.5, None, True, ["POLICY_QA"], {"a": 1}, {}]
    for value in cases:
        im = _im_envelope(app, _im_payload("fixed-1", BLIND_TEXT, intent=value))
        assert im.declared_intent is None, value
        email = _email_envelope(
            app,
            {
                "message_id": "fixed-2",
                "from": "alice@example.com",
                "text": BLIND_TEXT,
                "intent": value,
            },
        )
        assert email.declared_intent is None, value
        web = _web_envelope(app, {"message": BLIND_TEXT, "intent": value})
        assert web.declared_intent is None, value
    missing = _im_envelope(app, _im_payload("fixed-1", BLIND_TEXT))
    assert missing.declared_intent is None


def test_envelope_digest_is_byte_identical_after_the_new_field(
    tmp_path: Path,
) -> None:
    app, _ = _channels_client(tmp_path)
    # 改动前的字面量：同一构造（不带 intent）在改动后必须逐字节相等。
    assert envelope_digest(
        _im_envelope(app, _im_payload("fixed-1", BLIND_TEXT))
    ) == PRE_CHANGE_DIGESTS["im"]
    assert envelope_digest(
        _email_envelope(
            app,
            {
                "message_id": "fixed-2",
                "from": "alice@example.com",
                "text": BLIND_TEXT,
            },
        )
    ) == PRE_CHANGE_DIGESTS["email"]
    assert envelope_digest(
        _web_envelope(app, {"message": BLIND_TEXT})
    ) == PRE_CHANGE_DIGESTS["web"]
    # 带声明值的同一封邮件与不带声明值的同一封邮件摘要相同：字段不在 material 里。
    with_intent = _im_envelope(
        app, _im_payload("fixed-1", BLIND_TEXT, intent="POLICY_QA")
    )
    without_intent = _im_envelope(app, _im_payload("fixed-1", BLIND_TEXT))
    assert envelope_digest(with_intent) == envelope_digest(without_intent)


def test_declared_intent_is_not_carried_in_metadata(tmp_path: Path) -> None:
    app, _ = _channels_client(tmp_path)
    envelope = _im_envelope(
        app, _im_payload("fixed-1", BLIND_TEXT, intent="POLICY_QA")
    )
    assert "intent" not in dict(envelope.metadata)
    assert "declared_intent" not in dict(envelope.metadata)


# ------------------------------------------------------- DoD 9/7：接线与边界
def test_webhook_declared_intent_reaches_the_route(tmp_path: Path) -> None:
    _, client = _channels_client(tmp_path)
    response = _post_webhook(
        client,
        "wecom",
        _im_payload(f"im-{uuid4().hex}", BLIND_TEXT, intent="POLICY_QA"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["intent"] == "POLICY_QA"


def test_webhook_without_declaration_keeps_today_behaviour(tmp_path: Path) -> None:
    _, client = _channels_client(tmp_path)
    response = _post_webhook(
        client, "wecom", _im_payload(f"im-{uuid4().hex}", BLIND_TEXT)
    )
    assert response.status_code == 200, response.text
    assert response.json()["intent"] == "UNKNOWN"


def test_webhook_declared_write_intent_stays_unknown(tmp_path: Path) -> None:
    _, client = _channels_client(tmp_path)
    for declared in ("REFUND_REQUEST", "TICKET_CREATE", "DELIVERY_ISSUE", "policy_qa"):
        response = _post_webhook(
            client,
            "wecom",
            _im_payload(f"im-{uuid4().hex}", BLIND_TEXT, intent=declared),
        )
        assert response.status_code == 200, response.text
        assert response.json()["intent"] == "UNKNOWN", declared


def test_accept_web_does_not_take_external_declarations(tmp_path: Path) -> None:
    """T6-7 边界：网页入口的两个 handle 调用点不传声明值。"""
    _, client = _channels_client(tmp_path)
    response = client.post(
        "/api/v1/channels/web/messages",
        headers={**ALICE, "X-Channel-Message-Id": f"web-{uuid4().hex}"},
        json={"message": BLIND_TEXT, "intent": "POLICY_QA"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["intent"] == "UNKNOWN"


def test_silent_call_site_never_sees_the_declaration(tmp_path: Path) -> None:
    from app.core.security import Identity

    app, client = _channels_client(tmp_path)
    takeover = client.post(
        "/api/v1/support/messages", headers=ALICE, json={"message": "转人工"}
    )
    assert takeover.status_code == 200, takeover.text
    conversation_id = takeover.json()["conversation_id"]

    result = app.state.support_agent.handle(
        Identity(tenant_id="demo", user_id="alice", roles=frozenset()),
        BLIND_TEXT,
        conversation_id=conversation_id,
        declared_intent="POLICY_QA",
    )
    assert result["agent_silent"] is True
    # _silent 的 classify_support 调用点沿用默认 None：行为逐字不变。
    assert result["intent"] == "UNKNOWN"


# ------------------------------------------------- DoD 11：零留痕扫描（T6-8）
def _conversation_trace_spans(app: FastAPI, conversation_id: str) -> list[dict[str, Any]]:
    with app.state.session_factory() as session:
        rows = list(
            session.scalars(
                select(TraceSpan).where(TraceSpan.conversation_id == conversation_id)
            )
        )
    return [dict(row.attributes or {}) for row in rows]


def test_declared_value_leaves_no_trace_of_its_own(tmp_path: Path) -> None:
    app, client = _channels_client(tmp_path)
    message_id = f"im-{uuid4().hex}"
    response = _post_webhook(
        client,
        "wecom",
        _im_payload(message_id, BLIND_TEXT, intent="POLICY_QA"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    conversation_id = body["conversation_id"]
    assert body["intent"] == "POLICY_QA"  # 唯一可观察面：路由读数本身

    surfaces: list[dict[str, Any]] = [dict(body)]
    surfaces.extend(_conversation_trace_spans(app, conversation_id))
    with app.state.session_factory() as session:
        deliveries = list(
            session.scalars(
                select(ChannelMessage).where(
                    ChannelMessage.channel_message_id == message_id
                )
            )
        )
        surfaces.extend(
            dict(row.response_payload or {}) for row in deliveries
        )
        audits = list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.resource_id == conversation_id)
            )
        )
        surfaces.extend(dict(row.detail or {}) for row in audits)
        tasks = list(session.scalars(select(Task)))
    surfaces.extend(dict(task.context or {}) for task in tasks)

    for surface in surfaces:
        for path, key, leaf in _walk_pairs(surface):
            # ①没有任何键叫 declared_intent（或任何带 declared 的名字）。
            assert "declared" not in key.lower(), (path, key)
            # ②声明值只允许作为**路由读数**出现：intent 键，或既有的会话槽位
            #    last_intent / pending_intent（规则路径同样把它们写成 plan.intent
            #    的值，不是为声明新增的载体）。除此之外检索不到该值。
            if leaf == "POLICY_QA":
                assert key in {"intent", "last_intent", "pending_intent"}, (
                    path,
                    key,
                )

    # R7 四计数键不落盘；本回合直取不经采纳面，也没有 ModelDecision 可挂的任务。
    forbidden_keys = {
        "model_adopted",
        "model_redirect",
        "model_redirect_rejected",
        "model_redirect_kind",
    }
    for surface in surfaces:
        for _, key, _ in _walk_pairs(surface):
            assert key not in forbidden_keys
    assert tasks == []


def test_classify_ignores_a_context_declaration() -> None:
    # T6-9：任务 API 的 context 是调用方可控字典，不得喂进任何路由通路。
    route = classify(BLIND_TEXT, {"intent": "POLICY_QA"})
    assert route.intent is Intent.UNKNOWN
    plan = classify_support(
        BLIND_TEXT, route, has_catalog_match=False, declared_intent=None
    )
    assert plan.intent is SupportIntent.UNKNOWN


# --------------------------------------------- DoD 13：F-1 与转人工的登记代价
class _ExplodingBrain:
    """直取轮 gate 为 rules_hit：模型通路连一次都不许被敲。"""

    def plan(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        *,
        decision: Any = None,
        order_state: str | None = None,
    ) -> tuple[RoutePlan, str, Any]:
        raise AssertionError(f"declared-intent turn asked the model to plan: {request!r}")

    def probe_question(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        *,
        order_state: str | None = None,
    ) -> Any:
        raise AssertionError(f"declared-intent turn paid for a probe: {request!r}")


def _pydantic_app(tmp_path: Path, brain: Any) -> FastAPI:
    application = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+pysqlite:///{tmp_path / 'cq.db'}",
            auto_create_tables=True,
            agent_mode="pydantic_ai",
        ),
        brain=brain,
    )
    seed_all(application.state.session_factory)
    return application


def test_declared_intent_turn_forms_no_counter_question(tmp_path: Path) -> None:
    from app.core.security import Identity

    app = _pydantic_app(tmp_path, _ExplodingBrain())
    result = app.state.support_agent.handle(
        Identity(tenant_id="demo", user_id="alice", roles=frozenset()),
        BLIND_TEXT,
        declared_intent="POLICY_QA",
    )
    assert result["intent"] == "POLICY_QA"
    # 形成侧守卫的前置事实：plan.intent 非 UNKNOWN，反问不成立。
    assert result["clarification"] is None or (
        result["clarification"] or {}).get("reason") != "COUNTER_QUESTION_INTENT_AMBIGUOUS"


def test_model_gate_is_rules_hit_for_an_adopted_reading() -> None:
    route = RoutePlan(intent=Intent.UNKNOWN)
    filled = SupportPlan(SupportIntent.POLICY_QA)
    assert model_gate(filled, route, BLIND_TEXT) == "rules_hit"
    blind = SupportPlan(SupportIntent.UNKNOWN)
    assert model_gate(blind, route, BLIND_TEXT) == "question"


def test_declared_human_handoff_only_produces_the_existing_handoff_shape(
    tmp_path: Path,
) -> None:
    app, client = _channels_client(tmp_path)
    with app.state.session_factory() as session:
        tasks_before = int(session.scalar(select(func.count()).select_from(Task)) or 0)
    from app.models.handoff import HandoffEvent
    from app.services.handoff import TAKEOVER_STATES

    with app.state.session_factory() as session:
        events_before = int(
            session.scalar(select(func.count()).select_from(HandoffEvent)) or 0
        )

    rule_path = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": "转人工", "conversation_id": "hr-rule"},
    )
    assert rule_path.status_code == 200, rule_path.text
    declared = _post_webhook(
        client,
        "wecom",
        _im_payload(f"im-{uuid4().hex}", BLIND_TEXT, intent="HUMAN_HANDOFF"),
    )
    assert declared.status_code == 200, declared.text

    assert declared.json()["intent"] == "HUMAN_HANDOFF"
    rule_handoff = rule_path.json()["handoff"]
    assert rule_handoff is not None and rule_handoff["reason"] == "USER_REQUEST"

    with app.state.session_factory() as session:
        tasks_after = int(session.scalar(select(func.count()).select_from(Task)) or 0)
        new_events = list(
            session.scalars(
                select(HandoffEvent).order_by(HandoffEvent.created_at)
            )
        )[events_before:]
    # 两回合各产生一行转人工事件，此外业务表零增量：不动钱、HMAC 与身份校验一行不改。
    assert len(new_events) == 2
    assert all(event.status in {s.value for s in TAKEOVER_STATES} for event in new_events)
    assert all(event.reason == "USER_REQUEST" for event in new_events)
    assert tasks_after == tasks_before


def test_hmac_verification_is_untouched_for_declared_turns(tmp_path: Path) -> None:
    _, client = _channels_client(tmp_path)
    body = json.dumps(
        _im_payload("im-unsigned", BLIND_TEXT, intent="POLICY_QA"),
        ensure_ascii=False,
    ).encode("utf-8")
    unsigned = client.post(
        "/api/v1/channels/wecom/webhook",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert unsigned.status_code == 401
    signed = client.post(
        "/api/v1/channels/wecom/webhook",
        content=body,
        headers=_signed(body),
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["intent"] == "POLICY_QA"


# ------------------------------------------------------- 结构性钉桩（防漂移）
def test_classify_support_has_exactly_two_call_sites() -> None:
    import inspect
    import re

    source = inspect.getsource(support_agent_module)
    call_sites = re.findall(r"classify_support\(", source)
    # 定义 1 处 + 既有调用 2 处（handle 穿透与 _silent），不多不少。
    assert len(call_sites) == 3, call_sites
