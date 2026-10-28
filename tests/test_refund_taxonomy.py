"""退款原因 / 退款类型口径的落地测试。

口径来源：`.qoder/rules/refund-reason-taxonomy.md` §1.1、§1.2、§2、§4.1、§5.1-§5.3、
§5.6。本文件随任务逐段追加断言。
"""

from __future__ import annotations

import argparse
import inspect
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Enum as SAEnum
from sqlalchemy import create_engine, select, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import StatementError

from app.models.task import Task
from tests.conftest import ALICE

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_HEAD = "a1c7e9d4b820"


def test_refund_reason_members_are_exactly_the_taxonomy_ones() -> None:
    from app.models.enums import RefundReason

    assert [member.name for member in RefundReason] == [
        "QUALITY",
        "DAMAGED",
        "WRONG_ITEM",
        "NOT_AS_DESCRIBED",
        "NO_LONGER_NEEDED",
        "OTHER",
    ]
    assert [member.value for member in RefundReason] == [
        member.name for member in RefundReason
    ]


def test_refund_type_members_are_exactly_the_taxonomy_ones() -> None:
    from app.models.enums import RefundType

    assert [member.name for member in RefundType] == [
        "FULL",
        "PARTIAL",
        "SHIPPING",
        "PRICE_PROTECTION",
    ]
    assert [member.value for member in RefundType] == [
        member.name for member in RefundType
    ]


def test_both_enums_follow_the_existing_str_enum_style() -> None:
    from app.models.enums import RefundReason, RefundStatus, RefundType

    assert issubclass(RefundReason, StrEnum)
    assert issubclass(RefundType, StrEnum)
    assert issubclass(RefundStatus, StrEnum)
    assert RefundReason.QUALITY == "QUALITY"
    assert RefundType.FULL == "FULL"


def test_members_are_reachable_by_value_and_invalid_values_are_rejected() -> None:
    from app.models.enums import RefundReason, RefundType

    assert RefundReason("DAMAGED") is RefundReason.DAMAGED
    assert RefundType("PRICE_PROTECTION") is RefundType.PRICE_PROTECTION
    with pytest.raises(ValueError):
        RefundReason("NOT_A_REASON")
    with pytest.raises(ValueError):
        RefundType("NOT_A_TYPE")


# --- 任务 2：refunds 三列与一条迁移 ---------------------------------------


def _alembic_config(database_url: str) -> object:
    from alembic.config import Config

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.cmd_opts = argparse.Namespace(x=[f"db_url={database_url}"], tag=None)
    return config


def _seed_legacy_refund(database_url: str) -> None:
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO orders (id, tenant_id, user_id, status, total_cents,"
                " refundable_cents, currency, paid_at, version, created_at,"
                " updated_at) VALUES ('ORD-LEGACY', 'demo', 'alice', 'DELIVERED',"
                " 1000, 1000, 'CNY', '2026-01-01 00:00:00', 1, '2026-01-01 00:00:00',"
                " '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO refunds (id, order_id, tenant_id, user_id, amount_cents,"
                " status, provider, external_refund_id, idempotency_key, created_at,"
                " updated_at) VALUES ('rfd_legacy', 'ORD-LEGACY', 'demo', 'alice',"
                " 100, 'REQUESTED', 'mock', NULL, 'refund:legacy',"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
    engine.dispose()


def test_migration_adds_three_nullable_columns_without_backfilling(
    tmp_path: Path,
) -> None:
    from alembic import command

    database_url = f"sqlite+pysqlite:///{(tmp_path / 'migration.db').as_posix()}"
    config = _alembic_config(database_url)
    command.upgrade(config, PREVIOUS_HEAD)
    _seed_legacy_refund(database_url)

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    columns = {column["name"]: column for column in sa_inspect(engine).get_columns("refunds")}
    for name in ("reason", "refund_type", "reason_note"):
        assert name in columns, f"迁移后 refunds 缺少列 {name}"
        assert columns[name]["nullable"] is True, f"{name} 必须可空"
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT reason, refund_type, reason_note FROM refunds WHERE id = 'rfd_legacy'")
        ).one()
    assert row == (None, None, None), "迁移不得回填历史行"
    engine.dispose()


def test_migration_downgrade_removes_the_three_columns(tmp_path: Path) -> None:
    from alembic import command

    database_url = f"sqlite+pysqlite:///{(tmp_path / 'downgrade.db').as_posix()}"
    config = _alembic_config(database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    upgraded = {column["name"] for column in sa_inspect(engine).get_columns("refunds")}
    assert {"reason", "refund_type", "reason_note"} <= upgraded

    # 目标写死为这条迁移的父版本，不用相对值 "-1"：R9-c 之后 head 变成
    # ea6c2b48d701（task conversation link），"-1" 只会退掉 head 那一条，
    # 本用例要退的是 d9e4c7a15b82。断言本身不变。
    command.downgrade(config, PREVIOUS_HEAD)

    columns = {column["name"] for column in sa_inspect(engine).get_columns("refunds")}
    assert "reason" not in columns
    assert "refund_type" not in columns
    assert "reason_note" not in columns
    engine.dispose()


def test_model_declares_three_nullable_columns_with_enum_validation() -> None:
    from app.models.commerce import Refund
    from app.models.enums import RefundReason, RefundType

    table = Refund.__table__
    assert table.c["reason"].nullable is True
    assert table.c["refund_type"].nullable is True
    assert table.c["reason_note"].nullable is True
    reason_type = table.c["reason"].type
    refund_type = table.c["refund_type"].type
    assert isinstance(reason_type, SAEnum)
    assert isinstance(refund_type, SAEnum)
    assert list(reason_type.enums) == [member.value for member in RefundReason]
    assert list(refund_type.enums) == [member.value for member in RefundType]


def test_model_rejects_an_invalid_refund_reason_value(app: object) -> None:
    from app.models.commerce import Order, Refund
    from app.models.enums import RefundStatus

    session = app.state.session_factory()  # type: ignore[attr-defined]
    try:
        order = Order(
            id="ORD-TAXONOMY",
            tenant_id="demo",
            user_id="alice",
            status="DELIVERED",
            total_cents=1000,
            refundable_cents=1000,
            currency="CNY",
            paid_at=datetime(2026, 1, 1),
            version=1,
        )
        session.add(order)
        session.commit()
        session.add(
            Refund(
                id="rfd_bad_reason",
                order_id="ORD-TAXONOMY",
                tenant_id="demo",
                user_id="alice",
                amount_cents=100,
                status=RefundStatus.REQUESTED,
                provider="mock",
                idempotency_key="refund:bad-reason",
                reason="NOT_A_REASON",
            )
        )
        with pytest.raises(StatementError) as excinfo:
            session.commit()
        assert "NOT_A_REASON" in str(excinfo.value)
    finally:
        session.rollback()
        session.close()


# --- 任务 5：链路载体与对话侧采集 -------------------------------------------


def test_the_route_plan_carries_the_refund_reason_from_the_context() -> None:
    from app.agent.routing import classify
    from app.models.enums import RefundReason

    plan = classify("我要退款", {"refund_reason": "DAMAGED"})

    assert plan.refund_reason is RefundReason.DAMAGED
    assert classify("我要退款", {}).refund_reason is None


def test_the_route_plan_carries_the_refund_type_from_the_context() -> None:
    from app.agent.routing import classify
    from app.models.enums import RefundType

    plan = classify("我要退款", {"refund_type": "PARTIAL"})

    assert plan.refund_type is RefundType.PARTIAL
    assert classify("我要退款", {}).refund_type is None


def test_the_rule_table_fills_the_slot_and_a_miss_fills_nothing() -> None:
    from app.agent.routing import classify
    from app.models.enums import RefundReason

    hit = classify("我要退款，商品有质量问题", {})

    assert hit.refund_reason is RefundReason.QUALITY
    miss = classify("我要退款，订单 ORD-DEMO-1001，金额 129 元", {})
    assert miss.refund_reason is None


def test_an_explicit_other_is_a_choice_but_a_miss_is_not_other() -> None:
    from app.agent.routing import classify
    from app.models.enums import RefundReason

    # 退款回合里的显式「其他 / 说不清」才是一次分类选择（口径 §5.6.1.1）。
    assert classify("我要退款，其他原因", {}).refund_reason is RefundReason.OTHER
    assert classify("我要退款，说不清", {}).refund_reason is RefundReason.OTHER
    assert classify("随便聊聊天气", {}).refund_reason is None
    # 问题 1 设界：非退款回合根本不跑规则表——「还有其他优惠吗」里的
    # 「其他」不是任何一笔退款的原因选择（口径 §1「发起退款时」、§5.6.2）。
    assert classify("还有其他优惠吗", {}).refund_reason is None


def test_a_context_reason_is_kept_and_not_rematched() -> None:
    from app.agent.routing import classify
    from app.models.enums import RefundReason

    plan = classify("商品破损了", {"refund_reason": "QUALITY"})

    assert plan.refund_reason is RefundReason.QUALITY


def test_the_model_never_extracts_a_refund_reason_or_type() -> None:
    """口径 §5.6.5：模型不抽取、不采纳这两个值。

    断言从「整个文件不出现这两个名字」收窄成口径真正禁止的三件事：输出类型
    没有这两个字段、提示词不去要它们、采纳面不为它们开补位。文件里出现这两个
    名字本身不是违规——任务 6 要求 RoutePlan 原样搬运规则读数，那不是模型提供
    的值。
    """

    from app.agent.pydantic_agent import INSTRUCTIONS, IntentOutput

    fields = set(IntentOutput.model_fields)
    assert "refund_reason" not in fields
    assert "refund_type" not in fields
    assert "退款原因" not in INSTRUCTIONS
    assert "退款类型" not in INSTRUCTIONS
    source = Path(inspect.getsourcefile(IntentOutput) or "").read_text(encoding="utf-8")
    # 不得出现 `rule_plan.refund_reason or ...` 这类补位。
    assert "refund_reason or" not in source
    assert "refund_type or" not in source
    assert "output.refund_reason" not in source
    assert "output.refund_type" not in source
    assert "model_refund_reason" not in source
    assert "model_refund_type" not in source


def test_the_conversation_writes_the_matched_reason_into_the_task_context(
    client: TestClient, app: FastAPI
) -> None:
    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={
            "message": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题"
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["clarification"] is None
    with app.state.session_factory() as session:
        task = session.execute(select(Task)).scalar_one()
        assert task.context["refund_reason"] == "QUALITY"


def test_a_conversation_without_a_reason_asks_for_one_and_creates_no_task(
    client: TestClient, app: FastAPI
) -> None:
    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    clarification = body["clarification"]
    assert clarification is not None
    assert clarification["expected_slot"] == "refund_reason"
    assert "其他" in clarification["prompt"]
    with app.state.session_factory() as session:
        assert session.execute(select(Task)).scalar_one_or_none() is None


def test_the_task_context_carries_the_reason_only_when_one_was_determined(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """口径 §5.6.2：命中即填槽；未命中**不填**——键根本不出现。

    观察点是真正交给写路径的 context（``runtime.create_task`` 的实参），
    不是「取不到就是 None」那种读法。两颗牙：

    * 命中时键必须在——把写入那两行删掉即红；
    * 未命中时键必须不在——把它改成写 None / 空串即红。
    """
    from app.services.runtime import TaskRuntime

    captured: list[dict[str, Any]] = []
    original = TaskRuntime.create_task

    def spy(self, identity, request, context=None, **kwargs):  # type: ignore[no-untyped-def]
        captured.append(dict(context or {}))
        return original(self, identity, request, context, **kwargs)

    monkeypatch.setattr(TaskRuntime, "create_task", spy)

    hit = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={
            "message": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题"
        },
    )
    assert hit.status_code == 200, hit.text
    miss = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": "我要投诉，订单 ORD-DEMO-1001"},
    )
    assert miss.status_code == 200, miss.text

    assert len(captured) == 2, captured
    # 命中：键在，值是规则表读出来的原因。
    assert captured[0]["refund_reason"] == "QUALITY"
    # 未命中：键不出现。退款路径缺原因会先追问，因此观察点取同一条写路径上的
    # 工单任务——那里同样带着这个 context。
    assert "refund_reason" not in captured[1]


# --- 任务 6：写路径（载荷 / 工具入参 / 缺值闸门 / 类型派生 / 任务 API 422） ------

# 命中规则表 QUALITY 行的一句话（口径 §5.6.1）。
REFUND_WITH_REASON = "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题"
# 同一句话去掉原因：槽位缺失，两个入口各自表现（任务 API 422 / 对话追问）。
REFUND_WITHOUT_REASON = "我要申请退款，订单 ORD-DEMO-1001，金额 129 元"
# 金额小于订单可退余额（12900 分），派生类型应为 PARTIAL 而不是 FULL。
PARTIAL_REFUND_WITH_REASON = "我要申请退款，订单 ORD-DEMO-1001，金额 50 元，商品有质量问题"


def _payload_order() -> Any:
    from app.models.commerce import Order

    return Order(
        id="ORD-DEMO-1001",
        tenant_id="demo",
        user_id="alice",
        status="DELIVERED",
        total_cents=12900,
        refundable_cents=12900,
        currency="CNY",
        paid_at=datetime(2026, 1, 1),
        version=1,
    )


def _runtime_create(
    app: FastAPI, request: str, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """直接走运行时的写路径，绕开任务 API 的 422。

    缺值时任务 API 与对话入口的表现不同（422 / 追问），所以退款写路径本身
    必须能被单独观察：这里调的就是两个入口共用的那一个。
    """
    from app.core.security import Identity

    identity = Identity(tenant_id="demo", user_id="alice", roles=frozenset())
    return app.state.runtime.create_task(identity, request, context)


def _approvals(app: FastAPI) -> list[Any]:
    from app.models.approval import Approval

    with app.state.session_factory() as session:
        return list(session.scalars(select(Approval)))


def test_the_refund_payload_carries_the_reason_and_the_type() -> None:
    from app.core.approval_tiers import get_approval_tiers
    from app.models.enums import RefundReason, RefundType
    from app.services.approvals import refund_payload

    payload = refund_payload(
        _payload_order(),
        12900,
        "POLICY-2026-10",
        refund_reason=RefundReason.QUALITY,
        refund_type=RefundType.FULL,
        approval_tier=get_approval_tiers().tier_for(12900).snapshot(),
    )

    assert set(payload) == {
        "action",
        "order_id",
        "amount_cents",
        "currency",
        "resource_version",
        "policy_version",
        "reason",
        "refund_type",
        # 第九键 approval_tier 由 B-3 口径 §5.2 对上方八键清单的修订引入。
        "approval_tier",
    }
    assert payload["reason"] == "QUALITY"
    assert payload["refund_type"] == "FULL"


def test_the_payload_takes_two_context_named_keyword_arguments() -> None:
    from app.services.approvals import refund_payload

    params = inspect.signature(refund_payload).parameters
    assert "refund_reason" in params
    assert "refund_type" in params
    assert "reason" not in params
    assert params["refund_reason"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["refund_type"].kind is inspect.Parameter.KEYWORD_ONLY


def test_two_reasons_produce_two_digests() -> None:
    from app.core.approval_tiers import get_approval_tiers
    from app.models.enums import RefundReason, RefundType
    from app.services.approvals import canonical_digest, refund_payload

    order = _payload_order()
    tier_snapshot = get_approval_tiers().tier_for(12900).snapshot()
    quality = refund_payload(
        order,
        12900,
        "POLICY-2026-10",
        refund_reason=RefundReason.QUALITY,
        refund_type=RefundType.FULL,
        approval_tier=tier_snapshot,
    )
    damaged = refund_payload(
        order,
        12900,
        "POLICY-2026-10",
        refund_reason=RefundReason.DAMAGED,
        refund_type=RefundType.FULL,
        approval_tier=tier_snapshot,
    )

    assert canonical_digest(quality) != canonical_digest(damaged)


def test_create_refund_approval_takes_the_same_two_keyword_names() -> None:
    from app.services.approvals import ApprovalService

    params = inspect.signature(ApprovalService.create_refund_approval).parameters
    assert "refund_reason" in params
    assert "refund_type" in params
    assert "reason" not in params


def test_the_eligibility_tool_declares_the_two_optional_arguments(app: FastAPI) -> None:
    from app.models.enums import RefundReason, RefundType

    with app.state.session_factory() as session:
        registry = app.state.runtime._services(session).gateway.registry
    params = registry.require("check_refund_eligibility").manifest.params

    properties = params["properties"]
    assert "refund_reason" in properties
    assert "refund_type" in properties
    assert "reason" not in properties
    assert properties["refund_reason"]["enum"] == [member.value for member in RefundReason]
    assert properties["refund_type"]["enum"] == [member.value for member in RefundType]
    assert params["required"] == ["order_id"]
    assert params["additionalProperties"] is False


def test_a_refund_without_a_reason_asks_for_one_and_creates_no_approval(
    app: FastAPI,
) -> None:
    from app.models.commerce import Refund

    task = _runtime_create(app, REFUND_WITHOUT_REASON)

    assert task["status"] == "SUCCEEDED"
    assert task["result"]["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert _approvals(app) == []
    with app.state.session_factory() as session:
        assert list(session.scalars(select(Refund))) == []


def test_a_refund_without_a_type_derives_it_and_still_waits_for_approval(
    app: FastAPI,
) -> None:
    task = _runtime_create(app, REFUND_WITH_REASON)

    assert task["status"] == "WAITING_APPROVAL"
    approvals = _approvals(app)
    assert len(approvals) == 1
    assert approvals[0].action_payload["reason"] == "QUALITY"
    assert approvals[0].action_payload["refund_type"] == "FULL"


def test_a_partial_refund_derives_the_partial_type(app: FastAPI) -> None:
    task = _runtime_create(app, PARTIAL_REFUND_WITH_REASON)

    assert task["status"] == "WAITING_APPROVAL"
    approvals = _approvals(app)
    assert len(approvals) == 1
    assert approvals[0].action_payload["refund_type"] == "PARTIAL"


def test_an_explicit_type_is_adopted_and_not_overwritten(app: FastAPI) -> None:
    task = _runtime_create(app, REFUND_WITH_REASON, {"refund_type": "SHIPPING"})

    assert task["status"] == "WAITING_APPROVAL"
    approvals = _approvals(app)
    assert len(approvals) == 1
    assert approvals[0].action_payload["refund_type"] == "SHIPPING"


def test_the_eligibility_tool_is_called_with_the_reason_and_no_type(app: FastAPI) -> None:
    from app.models.observability import TraceSpan

    task = _runtime_create(app, REFUND_WITH_REASON)

    with app.state.session_factory() as session:
        spans = list(
            session.scalars(
                select(TraceSpan).where(TraceSpan.trace_id == task["trace_id"])
            )
        )
    params = next(
        span.attributes["params"]
        for span in spans
        if span.name == "check_refund_eligibility"
    )
    assert params["refund_reason"] == "QUALITY"
    # 类型是派生的，派生发生在订单加载之后；显式值缺席时这个键根本不出现。
    assert "refund_type" not in params


def test_the_task_api_rejects_a_refund_without_a_reason(
    client: TestClient, app: FastAPI
) -> None:
    response = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={"request": REFUND_WITHOUT_REASON},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    with app.state.session_factory() as session:
        assert session.execute(select(Task)).scalar_one_or_none() is None


def test_the_task_api_never_backfills_a_missing_reason_as_other(
    client: TestClient, app: FastAPI
) -> None:
    response = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={"request": REFUND_WITHOUT_REASON},
    )

    assert response.status_code == 422, response.text
    assert "OTHER" not in response.text
    with app.state.session_factory() as session:
        assert session.execute(select(Task)).scalar_one_or_none() is None


def test_the_task_api_never_backfills_a_missing_type_as_full(
    client: TestClient, app: FastAPI
) -> None:
    response = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={"request": PARTIAL_REFUND_WITH_REASON},
    )

    assert response.status_code == 201, response.text
    approvals = _approvals(app)
    assert len(approvals) == 1
    assert approvals[0].action_payload["refund_type"] == "PARTIAL"


def test_the_task_api_rejects_an_illegal_explicit_reason(client: TestClient) -> None:
    response = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={"request": REFUND_WITH_REASON, "context": {"refund_reason": "NOT_A_REASON"}},
    )

    assert response.status_code == 422, response.text


def test_the_task_api_rejects_an_illegal_explicit_type(client: TestClient) -> None:
    response = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={"request": REFUND_WITH_REASON, "context": {"refund_type": "NOT_A_TYPE"}},
    )

    assert response.status_code == 422, response.text


def test_the_task_api_rejects_a_note_beside_a_reason_that_is_not_other(
    client: TestClient, app: FastAPI
) -> None:
    response = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={
            "request": REFUND_WITH_REASON,
            "context": {"reason_note": "坐席说可以退"},
        },
    )

    assert response.status_code == 422, response.text
    with app.state.session_factory() as session:
        assert session.execute(select(Task)).scalar_one_or_none() is None


def test_the_task_api_accepts_a_note_beside_the_other_reason(
    client: TestClient, app: FastAPI
) -> None:
    response = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={
            "request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，说不清",
            "context": {"refund_reason": "OTHER", "reason_note": "坐席说可以退"},
        },
    )

    assert response.status_code == 201, response.text
    approvals = _approvals(app)
    assert len(approvals) == 1
    assert approvals[0].action_payload["reason"] == "OTHER"


def test_the_runtime_refuses_a_note_beside_a_reason_that_is_not_other(
    app: FastAPI,
) -> None:
    task = _runtime_create(app, REFUND_WITH_REASON, {"reason_note": "坐席说可以退"})

    assert task["status"] == "SUCCEEDED"
    assert task["result"]["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert _approvals(app) == []


def test_both_entries_derive_the_same_type(client: TestClient, app: FastAPI) -> None:
    api = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={"request": PARTIAL_REFUND_WITH_REASON},
    )
    assert api.status_code == 201, api.text
    # 同一订单同时只能有一张在途审批；先撤掉第一张，第二条路径才走得到写路径。
    cancelled = client.post(
        f"/api/v1/tasks/{api.json()['task_id']}/cancel",
        headers=ALICE,
    )
    assert cancelled.status_code == 200, cancelled.text

    chat = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": PARTIAL_REFUND_WITH_REASON},
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["task"]["status"] == "WAITING_APPROVAL"

    types = {approval.action_payload["refund_type"] for approval in _approvals(app)}
    assert types == {"PARTIAL"}


class _StubAgent:
    """A model with a fixed reading; the reason never comes from it."""

    model_name = "stub"

    def __init__(self) -> None:
        self.calls = 0

    def parse(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        candidates: object = None,
    ) -> tuple[Any, Any]:
        from app.agent.pydantic_agent import IntentOutput, ModelTrace

        self.calls += 1
        return (
            IntentOutput(
                intent="REFUND_REQUEST",
                order_id=None,
                amount_cents=None,
                confidence=0.9,
                decision_summary="固定读数。",
            ),
            ModelTrace(requests=1),
        )


def test_the_brain_carries_the_reason_through_the_candidate_branch() -> None:
    from app.agent.pydantic_agent import IntentAgent, IntentBrain
    from app.models.enums import RefundReason

    brain = IntentBrain(mode="pydantic_ai", agent=cast("IntentAgent", _StubAgent()))

    plan, _source, _trace = brain.plan(REFUND_WITH_REASON, {})

    assert plan.refund_reason is RefundReason.QUALITY


def test_the_brain_carries_the_reason_through_the_reuse_branch() -> None:
    from app.agent.decision import ModelDecision, request_digest
    from app.agent.pydantic_agent import IntentAgent, IntentBrain
    from app.agent.routing import RULESET_FINGERPRINT, classify
    from app.models.enums import RefundReason

    brain = IntentBrain(mode="pydantic_ai", agent=cast("IntentAgent", _StubAgent()))
    context = {"refund_reason": "QUALITY"}
    rules = classify(REFUND_WITH_REASON, context)
    decision = ModelDecision(
        digest=request_digest(REFUND_WITH_REASON, context),
        ruleset=RULESET_FINGERPRINT,
        model_name=brain.model_name,
        rule_intent=rules.intent.value,
        rule_order_id=rules.order_id,
        rule_amount_cents=rules.amount_cents,
        model_intent=rules.intent.value,
        model_order_id=rules.order_id,
        model_amount_cents=rules.amount_cents,
        trace={},
    )

    plan, source, trace = brain.plan(REFUND_WITH_REASON, context, decision=decision)

    assert source == "pydantic_ai"
    assert trace is not None and trace.reused is True
    assert plan.refund_reason is RefundReason.QUALITY
    assert plan.refund_type is None


# --- 跨轮携带：上一轮已确认的原因沿用（口径 §5.6.1.1、§5.6.4）---------


def test_the_conversation_slot_carries_the_refund_reason() -> None:
    """会话槽位能装下退款原因，历史会话没有这个键时读出「没有」。

    槽位是 ``conversations.slots`` 里的一段 JSON，新增一个可空键不需要迁移。
    """
    from app.services.conversations import ConversationSlots

    slots = ConversationSlots(refund_reason="DAMAGED")

    assert slots.to_dict()["refund_reason"] == "DAMAGED"
    assert ConversationSlots.from_dict(slots.to_dict()).refund_reason == "DAMAGED"
    # 历史会话没有这个键：读出来是「没有」，不是别的值，也不做默认兜底。
    assert ConversationSlots.from_dict({"order_id": "ORD-DEMO-1001"}).refund_reason is None
    assert ConversationSlots.from_dict(None).refund_reason is None


def test_the_confirmed_reason_is_carried_into_the_next_turn(
    client: TestClient, app: FastAPI
) -> None:
    """第 1 轮说了原因，第 2 轮补订单号与金额：不该再问一次原因。

    口径 §5.6.1.1「对话侧的优先级」：上下文里已带合法 ``refund_reason``
    （上一轮已确认、客户本轮只是补充别的信息）时沿用该值，不重新匹配。
    """
    first = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": "商品有质量问题，我要申请退款"},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["clarification"]["expected_slot"] == "order_id"
    assert first_body["slots"]["refund_reason"] == "QUALITY"

    second = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={
            "message": "ORD-DEMO-1001 129 元",
            "conversation_id": first_body["conversation_id"],
        },
    )
    assert second.status_code == 200, second.text
    second_body = second.json()

    assert second_body["clarification"] is None
    assert second_body["task"]["status"] == "WAITING_APPROVAL"
    # 问题 2 的生命周期：本轮成功建单即本次沿用之路的终点，槽位被清除
    # （口径 §5.6.1.1「沿用不重新匹配」只适用于同一笔退款流程内）。
    # 沿用确实发生过的事实由任务 context 与审批载荷钉住（下面两条断言）。
    assert second_body["slots"]["refund_reason"] is None
    with app.state.session_factory() as session:
        task = session.execute(select(Task)).scalar_one()
        assert task.context["refund_reason"] == "QUALITY"


def test_a_carried_reason_is_not_rematched_by_the_next_message(
    client: TestClient,
) -> None:
    """同一退款流程内沿用：本轮文本命中规则表也不改写已确认的值（口径 §5.6.1.1）。

    turn2 带「我要申请退款」使本回合退款流程仍开着（规划意图为 REFUND_REQUEST、
    写回的 pending_intent 也是 REFUND_REQUEST），已确认的 QUALITY 因此沿用。
    插问非退款回合不属本用例形态，那一回合按 §5.6.1.1 代价句必须清掉原因，由
    `test_a_topic_change_turn_clears_the_carried_reason_for_the_next_refund` 钉住。
    """
    first = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": "商品有质量问题，我要申请退款"},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={
            "message": "包裹破损了，我要申请退款",
            "conversation_id": first.json()["conversation_id"],
        },
    )
    assert second.status_code == 200, second.text

    # turn2 文本命中 DAMAGED，但同一退款流程内上一轮确认过的是 QUALITY：沿用，不改写。
    assert second.json()["slots"]["refund_reason"] == "QUALITY"
    assert second.json()["slots"]["pending_intent"] == "REFUND_REQUEST"


# --- 返工轮（退回项 1 / 2）：退款回合设界 + 建单即清除 ------------------------


def test_a_non_refund_turn_does_not_latch_the_reason_into_the_slots(
    client: TestClient, app: FastAPI
) -> None:
    """问题 1：规则匹配只在规划为退款的回合发生（口径 §1、§5.6.2）。

    回合 A 的「还有其他优惠吗」命中 `other_cues` 的「其他」，但它不是退款
    回合，不得把 OTHER 锁进会话槽位；回合 B 正式发起退款（缺原因）时仍必须
    触发追问，且不建单。
    """
    first = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": "还有其他优惠吗"},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["slots"]["refund_reason"] is None

    second = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={
            "message": REFUND_WITHOUT_REASON,
            "conversation_id": first_body["conversation_id"],
        },
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    clarification = second_body["clarification"]
    assert clarification is not None
    assert clarification["expected_slot"] == "refund_reason"
    assert second_body["task"] is None
    with app.state.session_factory() as session:
        assert session.execute(select(Task)).scalar_one_or_none() is None


def test_the_carried_reason_is_cleared_once_the_refund_task_is_created(
    client: TestClient, app: FastAPI
) -> None:
    """问题 2：carry-over 的生命周期收敛为同一笔退款流程内（§5.6.1.1 生命周期句）。

    第一笔退款成功建单的回合清除 `ConversationSlots.refund_reason`；
    第二笔退款必须重新采集原因，不沿用第一笔的读数。`refund_type` 不受
    影响（它不进 required_slots，缺值走 §5.6.6 派生）。
    """
    first = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": REFUND_WITH_REASON},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["clarification"] is None
    assert first_body["task"]["status"] == "WAITING_APPROVAL"
    assert first_body["slots"]["refund_reason"] is None

    second = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={
            "message": "我要申请退款，订单 ORD-DEMO-1002，金额 50 元",
            "conversation_id": first_body["conversation_id"],
        },
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    clarification = second_body["clarification"]
    assert clarification is not None
    assert clarification["expected_slot"] == "refund_reason"
    assert second_body["task"] is None
    # 建单只发生过一次：第二笔被追问拦住，没有新增审批。
    assert len(_approvals(app)) == 1


def test_a_topic_change_turn_clears_the_carried_reason_for_the_next_refund(
    client: TestClient, app: FastAPI
) -> None:
    """B′ 订正：存活条件读「本回合写回的 pending_intent」（口径 §5.6.1.1）。

    三回合真实序列：turn1 破损退款缺金额追问，原因 DAMAGED 携带；turn2 插问
    一句物流（无追问、无建单）——按订正后的不变量，这一回合就把
    `refund_reason` 与 `pending_intent` 同时清掉（存活域读本轮写回值，不是
    上一回合存量的 `carried.pending_intent`）；turn3 一句不带任何原因词的退款
    请求必须重新被追问原因（`expected_slot == refund_reason`），不得被陈旧值
    喂饱直接建单。
    """
    first = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": "包裹破损了，我要申请退款，订单 ORD-DEMO-1001"},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["clarification"]["reason"] == "MISSING_AMOUNT"
    assert first_body["task"] is None
    assert first_body["slots"]["refund_reason"] == "DAMAGED"
    assert first_body["slots"]["pending_intent"] == "REFUND_REQUEST"

    second = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={
            "message": "订单 ORD-DEMO-1001 到哪里了",
            "conversation_id": first_body["conversation_id"],
        },
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["clarification"] is None
    # 不变量本体（口径 §5.6.1.1 本轮订正）：插问非退款回合即清。
    assert second_body["slots"]["refund_reason"] is None
    assert second_body["slots"]["pending_intent"] is None

    third = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={
            "message": "我要申请退款，订单 ORD-DEMO-1002，金额 50 元",
            "conversation_id": second_body["conversation_id"],
        },
    )
    assert third.status_code == 200, third.text
    third_body = third.json()
    assert third_body["clarification"]["expected_slot"] == "refund_reason"
    assert third_body["task"] is None
    with app.state.session_factory() as session:
        assert session.execute(select(Task)).scalar_one_or_none() is None
