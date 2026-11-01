"""R11 T4/T5-a：通知出口 `GET /api/v1/monitor/signals` 的行为与红线用例。

口径来源：`.qoder/rules/online-signals-and-notification.md` §4-1/§4-2/§4-3/§4-4、
§2 通用规则、§2-B 裁定 11（零分母在**出口层**映射为 `null`）、§5（触警留痕与
误报率）、§6 红线、§9 第 7/8/9/10/11/13/14 条。断言一律对着口径的字面契约，
不对着实现自证。

本文件同时钉住出口层边界：出口层**不直接**读审计台账（审计逻辑在服务层
`app/services/signal_audit.py`，由 `inspect.getsource` 用例钉住）、零模型调用、
`app/main.py` 恰 13 行 `include_router`、不新建 `router.py`。
reviewed 行的写入端点 `POST /api/v1/monitor/reviews` 与其服务层生产者已随 T5-b
落地：本文件既端到端走真实复核端点写 reviewed 行、也保留直接种审计行的消费侧
用例，双重验证误报率三态与行契约。
"""

from __future__ import annotations

import ast
import inspect
import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.errors import ForbiddenError
from app.core.security import Identity
from app.core.time import utcnow
from app.models.audit import AuditEvent
from app.models.conversation import Conversation, ConversationMessage
from app.models.enums import HandoffReason, ToolExecutionStatus
from app.models.handoff import HandoffEvent
from app.models.task import Task
from app.models.tool_execution import ToolExecution
from app.services.signal_readings import SIGNAL_IDS, collect_readings
from tests.conftest import ALICE

REPO_ROOT = Path(__file__).resolve().parents[1]

SIGNALS_URL = "/api/v1/monitor/signals"
REVIEW_URL = "/api/v1/monitor/reviews"

# 口径 §2 通用规则：窗口参数 `ge=1, le=60*24*90`。字面写在测试里，
# 不从实现取常量——否则实现把边界写错时测试会跟着一起错。
WINDOW_MIN = 1
WINDOW_MAX = 60 * 24 * 90

# 口径 §4-1 定死的九字段名与顺序（逐字）。
NINE_FIELDS = (
    "signal_id",
    "window_minutes",
    "numerator",
    "denominator",
    "metric_value",
    "threshold",
    "breached",
    "calibrated",
    "action_suggestion",
)

NO_EVIDENCE = "no_evidence_rate"
HANDOFF = "handoff_rate"
TOOL_FAILURE = "tool_failure_rate"


# ----------------------------------------------------------------- helpers


def _staff(tenant: str, role: str = "support", user: str = "op-1") -> dict[str, str]:
    return {"X-Tenant-Id": tenant, "X-User-Id": user, "X-Roles": role}


def _conv(session: Any, tenant: str, cid: str) -> None:
    moment = utcnow()
    session.add(
        Conversation(
            id=cid,
            tenant_id=tenant,
            user_id="u1",
            created_at=moment,
            updated_at=moment,
        )
    )


def _msg(session: Any, cid: str, *, intent: str = "POLICY_QA", role: str = "user") -> None:
    moment = utcnow()
    session.add(
        ConversationMessage(
            id=f"cm_{uuid.uuid4().hex[:16]}",
            conversation_id=cid,
            role=role,
            intent=intent,
            content="x",
            created_at=moment,
        )
    )


def _handoff(session: Any, tenant: str, cid: str, *, off_intent: bool = False) -> None:
    session.add(
        HandoffEvent(
            id=f"hnd_{uuid.uuid4().hex[:16]}",
            tenant_id=tenant,
            user_id="u1",
            conversation_id=cid,
            signal_id="other" if off_intent else "no_evidence",
            reason=HandoffReason.FRUSTRATION.value
            if off_intent
            else HandoffReason.NO_EVIDENCE.value,
            priority="P2",
            created_at=utcnow(),
        )
    )


def _task(session: Any, tenant: str, tid: str) -> None:
    session.add(Task(id=tid, tenant_id=tenant, user_id="u1", request="r"))


def _execs(
    session: Any, tid: str, status: ToolExecutionStatus, count: int, prefix: str
) -> None:
    for index in range(count):
        exec_id = f"{prefix}{index}_{uuid.uuid4().hex[:8]}"
        session.add(
            ToolExecution(
                id=exec_id,
                task_id=tid,
                tool_name="t",
                request_digest="d",
                idempotency_key=exec_id,
                status=status,
                created_at=utcnow(),
            )
        )


def _rows(client: TestClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = client.get(SIGNALS_URL, headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    signals: list[dict[str, Any]] = body["signals"]
    return signals


def _row(rows: list[dict[str, Any]], signal_id: str) -> dict[str, Any]:
    for row in rows:
        if row["signal_id"] == signal_id:
            return row
    raise AssertionError(f"missing signal row {signal_id!r}; got {[r['signal_id'] for r in rows]}")


# ------------------------------------------------------- 1. staff 200 / 403


def test_staff_roles_get_200_and_non_staff_get_403(client: TestClient) -> None:
    for role in ("admin", "approver", "support"):
        response = client.get(SIGNALS_URL, headers=_staff("iso-mon-auth", role))
        assert response.status_code == 200, role
        assert len(response.json()["signals"]) == 3, role

    forbidden = client.get(SIGNALS_URL, headers=ALICE)
    assert forbidden.status_code == 403
    error = forbidden.json()["error"]
    assert error["code"] == "FORBIDDEN"
    assert error["message"] == "Staff role required."


# ----------------------------------------------------- 2. 窗口参数越界


def test_window_minutes_bounds_are_enforced(client: TestClient) -> None:
    headers = _staff("iso-mon-window")

    assert client.get(SIGNALS_URL, headers=headers, params={"window_minutes": 0}).status_code == 422
    too_big = client.get(
        SIGNALS_URL, headers=headers, params={"window_minutes": WINDOW_MAX + 1}
    )
    assert too_big.status_code == 422

    smallest = client.get(
        SIGNALS_URL, headers=headers, params={"window_minutes": WINDOW_MIN}
    )
    assert smallest.status_code == 200
    assert all(row["window_minutes"] == WINDOW_MIN for row in smallest.json()["signals"])

    largest = client.get(
        SIGNALS_URL, headers=headers, params={"window_minutes": WINDOW_MAX}
    )
    assert largest.status_code == 200
    assert all(row["window_minutes"] == WINDOW_MAX for row in largest.json()["signals"])

    whole_history = client.get(SIGNALS_URL, headers=headers)
    assert whole_history.status_code == 200
    assert all(row["window_minutes"] is None for row in whole_history.json()["signals"])


# ------------------------------------------- 3. 三行、顺序、九字段逐字


def test_response_has_three_rows_in_order_with_the_nine_field_names(
    app: FastAPI, client: TestClient
) -> None:
    headers = _staff("iso-mon-shape")
    with app.state.session_factory() as session:
        _conv(session, "iso-mon-shape", "c1")
        _msg(session, "c1")
        session.commit()

    response = client.get(SIGNALS_URL, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"signals", "false_positive_rate"}

    rows = body["signals"]
    assert [row["signal_id"] for row in rows] == list(SIGNAL_IDS)
    for row in rows:
        assert tuple(row) == NINE_FIELDS


# -------------------------------------- 4. 搬运不重算（round 4 位口径）


def test_metric_values_are_carried_from_the_collector_not_recomputed(
    app: FastAPI, client: TestClient
) -> None:
    tenant = "iso-mon-carry"
    with app.state.session_factory() as session:
        _conv(session, tenant, "c1")
        for _ in range(4):
            _msg(session, "c1")
        _handoff(session, tenant, "c1")
        _task(session, tenant, "t1")
        _execs(session, "t1", ToolExecutionStatus.FAILED, 2, "fa")
        _execs(session, "t1", ToolExecutionStatus.SENT, 1, "se")
        session.commit()

    with app.state.session_factory() as session:
        collected = {
            reading.signal_id: reading
            for reading in collect_readings(
                session, Identity(tenant, "op-1", frozenset()), policy=app.state.policy
            )
        }

    for row in _rows(client, _staff(tenant)):
        reading = collected[row["signal_id"]]
        assert row["window_minutes"] == reading.window_minutes
        assert row["numerator"] == reading.numerator
        assert row["denominator"] == reading.denominator
        if reading.denominator > 0:
            # 出口层逐字搬运采集器已 round(4) 的读数：不重算、不二次 round。
            assert row["metric_value"] == reading.metric_value
            assert row["metric_value"] == round(reading.numerator / reading.denominator, 4)

    rows = _rows(client, _staff(tenant))
    assert _row(rows, NO_EVIDENCE)["metric_value"] == 0.25
    assert _row(rows, TOOL_FAILURE)["metric_value"] == 0.6667


# ------------------------------- 5. 零分母：出口 null 与既有端点 0.0 并存


def test_zero_denominator_handoff_row_is_null_while_legacy_metric_stays_zero(
    client: TestClient,
) -> None:
    headers = _staff("iso-mon-empty")
    rows = _rows(client, headers)
    handoff = _row(rows, HANDOFF)
    assert handoff["numerator"] == 0
    assert handoff["denominator"] == 0
    assert handoff["metric_value"] is None
    assert handoff["breached"] is False

    legacy = client.get("/api/v1/handoffs/metrics", headers=headers)
    assert legacy.status_code == 200
    legacy_body = legacy.json()
    assert legacy_body["conversations"] == 0
    # 裁定 11：出口层映射，不是第二份事实源——既有端点的 0.0 一行不改。
    assert legacy_body["handoff_rate"] == 0.0
    assert isinstance(legacy_body["handoff_rate"], float)


def test_zero_denominator_no_evidence_and_tool_failure_rows_are_null(
    client: TestClient,
) -> None:
    rows = _rows(client, _staff("iso-mon-empty-ac"))
    for signal_id in (NO_EVIDENCE, TOOL_FAILURE):
        row = _row(rows, signal_id)
        assert row["denominator"] == 0, signal_id
        assert row["metric_value"] is None, signal_id
        assert row["breached"] is False, signal_id


# ------------------------------------------- 6. breached 严格大于阈值


def test_breached_requires_strictly_greater_than_the_threshold(
    app: FastAPI, client: TestClient
) -> None:
    at_line = "iso-mon-at-threshold"
    above = "iso-mon-above-threshold"
    with app.state.session_factory() as session:
        _conv(session, at_line, "ceq1")
        for _ in range(5):  # A = 1/5 = 0.20，恰等于阈值
            _msg(session, "ceq1")
        _handoff(session, at_line, "ceq1")
        _task(session, at_line, "teq1")
        _execs(session, "teq1", ToolExecutionStatus.FAILED, 1, "af")  # C = 1/10 = 0.10
        _execs(session, "teq1", ToolExecutionStatus.SENT, 9, "as")
        _conv(session, above, "covr1")
        for _ in range(4):  # A = 1/4 = 0.25 > 0.20
            _msg(session, "covr1")
        _handoff(session, above, "covr1")
        session.commit()

    at_line_rows = _rows(client, _staff(at_line))
    assert _row(at_line_rows, NO_EVIDENCE)["metric_value"] == 0.2
    assert _row(at_line_rows, NO_EVIDENCE)["breached"] is False
    assert _row(at_line_rows, TOOL_FAILURE)["metric_value"] == 0.1
    assert _row(at_line_rows, TOOL_FAILURE)["breached"] is False

    above_rows = _rows(client, _staff(above))
    assert _row(above_rows, NO_EVIDENCE)["metric_value"] == 0.25
    assert _row(above_rows, NO_EVIDENCE)["breached"] is True


# --------------------------- 7. threshold/calibrated/action_suggestion 出自 YAML


def test_threshold_calibrated_and_action_suggestion_are_taken_from_the_yaml(
    client: TestClient,
) -> None:
    raw = yaml.safe_load(
        (REPO_ROOT / "app/core/online_signals.yaml").read_text(encoding="utf-8")
    )
    blocks: dict[str, dict[str, Any]] = {
        item["signal_id"]: item for item in raw["signals"]
    }

    rows = _rows(client, _staff("iso-mon-yaml"))
    for row in rows:
        block = blocks[row["signal_id"]]
        assert row["threshold"] == block["threshold"], row["signal_id"]
        assert row["calibrated"] is block["calibrated"], row["signal_id"]
        # 红线 §6-1：动作建议逐字等于 YAML 值，用字节相等断言，不硬编码文案自证。
        assert row["action_suggestion"].encode("utf-8") == str(
            block["action_suggestion"]
        ).encode("utf-8"), row["signal_id"]

    assert _row(rows, NO_EVIDENCE)["threshold"] == 0.2
    assert _row(rows, NO_EVIDENCE)["calibrated"] is False
    assert _row(rows, HANDOFF)["threshold"] == 0.55
    assert _row(rows, HANDOFF)["calibrated"] is True
    assert _row(rows, TOOL_FAILURE)["threshold"] == 0.1
    assert _row(rows, TOOL_FAILURE)["calibrated"] is False


# ------------------- 8. 误报率三态按审计行变化（口径 §5/§9-8；T5-a 重写）


def _seed_reviewed(
    session: Any, tenant: str, outcomes: tuple[str, ...], *, age_minutes: int = 0
) -> None:
    """种 reviewed 审计行——字面照口径 §5，不 import 实现的常量来自证。"""
    moment = utcnow() - timedelta(minutes=age_minutes)
    for index, outcome in enumerate(outcomes):
        session.add(
            AuditEvent(
                id=f"aud_{uuid.uuid4().hex[:12]}_{index}",
                actor_id="op-1",
                tenant_id=tenant,
                action="monitor.signal.reviewed",
                resource_type="monitor_signal",
                resource_id=HANDOFF,
                outcome=outcome,
                detail={},
                created_at=moment,
            )
        )


def test_false_positive_rate_follows_reviewed_audit_rows(
    app: FastAPI, client: TestClient
) -> None:
    tenant = "iso-mon-fpr"
    with app.state.session_factory() as session:
        _seed_reviewed(session, tenant, ("FALSE_POSITIVE", "CONFIRMED", "FALSE_POSITIVE"))
        session.commit()

    headers = _staff(tenant)
    first = client.get(SIGNALS_URL, headers=headers).json()
    # 口径 §5 公式：误报数 ÷ 已复核数 = 2/3 → round(x, 4)。
    assert first["false_positive_rate"] == 0.6667
    # 同一种子数据两次读取逐字相等：现算、无随机、不漂。
    second = client.get(SIGNALS_URL, headers=headers).json()
    assert second == first

    import app.api.v1.monitor as monitor_module

    source = inspect.getsource(monitor_module)
    for forbidden in ("AuditRepository", "app.models.audit", "app.repositories"):
        assert forbidden not in source, f"出口层不得触碰审计台账：{forbidden!r}"


def test_false_positive_rate_has_three_distinguishable_states(
    app: FastAPI, client: TestClient
) -> None:
    # §9-8：null（零复核）与 0.0（全对）必须都出现且可分；false 行出 round(x,4)。
    none_tenant = "iso-mon-tri-none"
    zero_tenant = "iso-mon-tri-zero"
    part_tenant = "iso-mon-tri-part"
    with app.state.session_factory() as session:
        _seed_reviewed(session, zero_tenant, ("CONFIRMED", "CONFIRMED"))
        _seed_reviewed(session, part_tenant, ("FALSE_POSITIVE", "CONFIRMED", "CONFIRMED"))
        session.commit()

    null_read = client.get(SIGNALS_URL, headers=_staff(none_tenant)).json()
    assert null_read["false_positive_rate"] is None

    zero_read = client.get(SIGNALS_URL, headers=_staff(zero_tenant)).json()
    assert zero_read["false_positive_rate"] == 0.0
    assert zero_read["false_positive_rate"] is not None  # 与 null 可分

    part_read = client.get(SIGNALS_URL, headers=_staff(part_tenant)).json()
    assert part_read["false_positive_rate"] == 0.3333


def test_false_positive_rate_is_scoped_to_its_own_tenant(
    app: FastAPI, client: TestClient
) -> None:
    # §9-13 相关：A 租户的 reviewed 行不得影响 B 租户的误报率。
    mine = "iso-mon-fpr-mine"
    theirs = "iso-mon-fpr-theirs"
    with app.state.session_factory() as session:
        _seed_reviewed(session, theirs, ("FALSE_POSITIVE", "CONFIRMED"))
        session.commit()

    mine_read = client.get(SIGNALS_URL, headers=_staff(mine)).json()
    assert mine_read["false_positive_rate"] is None

    theirs_read = client.get(SIGNALS_URL, headers=_staff(theirs)).json()
    assert theirs_read["false_positive_rate"] == 0.5


# ---------------- 9. breached 幂等去重与复警间隔（口径 §5/§9-9，断言行数）


def _breach_count(app: FastAPI, tenant: str, signal_id: str) -> int:
    with app.state.session_factory() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.tenant_id == tenant,
                    AuditEvent.action == "monitor.signal.breached",
                    AuditEvent.resource_type == "monitor_signal",
                    AuditEvent.resource_id == signal_id,
                )
            )
            or 0
        )


def _seed_breached(
    session: Any,
    tenant: str,
    signal_id: str,
    *,
    age_minutes: int,
    window_minutes: int,
) -> None:
    session.add(
        AuditEvent(
            id=f"aud_{uuid.uuid4().hex[:12]}_brk",
            actor_id="op-1",
            tenant_id=tenant,
            action="monitor.signal.breached",
            resource_type="monitor_signal",
            resource_id=signal_id,
            outcome="SUCCESS",
            detail={
                "signal_id": signal_id,
                "window_minutes": window_minutes,
                "metric_value": 0.25,
                "threshold": 0.2,
            },
            created_at=utcnow() - timedelta(minutes=age_minutes),
        )
    )


def test_repeated_reads_in_one_window_write_at_most_one_breach_row(
    app: FastAPI, client: TestClient
) -> None:
    tenant = "iso-mon-dedup"
    with app.state.session_factory() as session:
        _conv(session, tenant, "c1")
        for _ in range(4):  # A = 1/4 = 0.25 > 0.20 → 触警
            _msg(session, "c1")
        _handoff(session, tenant, "c1")  # B = 1/1 = 1.0 > 0.55 → 触警
        session.commit()

    headers = _staff(tenant)
    for _ in range(3):
        response = client.get(
            SIGNALS_URL, headers=headers, params={"window_minutes": 60}
        )
        assert response.status_code == 200

    # 负对照：不去重的实现会留下 3×2 行——这里断言恰 1+1。
    assert _breach_count(app, tenant, NO_EVIDENCE) == 1
    assert _breach_count(app, tenant, HANDOFF) == 1
    assert _breach_count(app, tenant, TOOL_FAILURE) == 0  # C 零分母不触警不写行


def test_breach_within_realert_interval_does_not_rewrite(
    app: FastAPI, client: TestClient
) -> None:
    # 既有行 10 分钟前：在 realert_minutes=15 之内、在 window_minutes=5 之外——
    # 只有「距最近一条不足 realert」这一支能拦住重写（钉住去重的第二条件）。
    tenant = "iso-mon-realert-suppress"
    with app.state.session_factory() as session:
        _conv(session, tenant, "c1")
        for _ in range(4):
            _msg(session, "c1")
        _handoff(session, tenant, "c1")
        _seed_breached(
            session, tenant, NO_EVIDENCE, age_minutes=10, window_minutes=60
        )
        session.commit()

    client.get(SIGNALS_URL, headers=_staff(tenant), params={"window_minutes": 5})
    assert _breach_count(app, tenant, NO_EVIDENCE) == 1


def test_breach_after_realert_interval_writes_a_new_row(
    app: FastAPI, client: TestClient
) -> None:
    # 既有行 20 分钟前：既超出 realert_minutes=15，也落在 window_minutes=5 之外——
    # 两个条件都不拦，必须写新行（§9-9「跨过再 breach 有新行」）。
    tenant = "iso-mon-realert-renew"
    with app.state.session_factory() as session:
        _conv(session, tenant, "c1")
        for _ in range(4):
            _msg(session, "c1")
        _handoff(session, tenant, "c1")
        _seed_breached(
            session, tenant, NO_EVIDENCE, age_minutes=20, window_minutes=60
        )
        session.commit()

    client.get(SIGNALS_URL, headers=_staff(tenant), params={"window_minutes": 5})
    assert _breach_count(app, tenant, NO_EVIDENCE) == 2


# --------- 10. breached 行契约：封闭键集，detail 不落客户文案与单号（§5/§9-10）


def test_breach_row_carries_only_the_closed_detail_keys(
    app: FastAPI, client: TestClient
) -> None:
    tenant = "iso-mon-detail"
    with app.state.session_factory() as session:
        _conv(session, tenant, "c1")
        for _ in range(4):
            _msg(session, "c1")
        _handoff(session, tenant, "c1")
        # 混入客户文案与订单号形态的会话消息（intent 不在 A 分母集合，不改读数）。
        moment = utcnow()
        session.add(
            ConversationMessage(
                id=f"cm_{uuid.uuid4().hex[:16]}",
                conversation_id="c1",
                role="user",
                intent="CHIT_CHAT",
                content="我的订单 ORD-77881234 还没退款，太气人了",
                created_at=moment,
            )
        )
        session.commit()

    response = client.get(
        SIGNALS_URL, headers=_staff(tenant), params={"window_minutes": 60}
    )
    assert response.status_code == 200

    with app.state.session_factory() as session:
        events = session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == tenant,
                AuditEvent.action == "monitor.signal.breached",
            )
            .order_by(AuditEvent.id)
        ).all()
        assert len(events) == 2  # A 与 B 各一条；恰 0 条会让下面的扫描变成空断言
        for event in events:
            assert event.actor_id == "op-1"
            assert event.outcome == "SUCCESS"
            assert event.resource_type == "monitor_signal"
            assert set(event.detail) == {
                "signal_id",
                "window_minutes",
                "metric_value",
                "threshold",
            }
            text = json.dumps(event.detail, ensure_ascii=False)
            assert "ORD-" not in text
            assert "还没退款" not in text
        by_id = {event.resource_id: event for event in events}
        assert by_id[NO_EVIDENCE].detail == {
            "signal_id": NO_EVIDENCE,
            "window_minutes": 60,
            "metric_value": 0.25,
            "threshold": 0.2,
        }
        assert by_id[HANDOFF].detail == {
            "signal_id": HANDOFF,
            "window_minutes": 60,
            "metric_value": 1.0,
            "threshold": 0.55,
        }


# ---------------------------------------------- 9. 零模型调用（§9-11）


def test_monitor_endpoint_makes_no_model_calls(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent.pydantic_agent import IntentAgent

    # 桩自证有效：符号不存在时 monkeypatch 会静默失效，断言先把住。
    assert hasattr(IntentAgent, "parse"), "IntentAgent.parse 已改名，桩会空断言"

    calls: list[tuple[Any, Any]] = []

    def _stub(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        raise AssertionError("R11 的 monitor 出口不得触发模型调用")

    monkeypatch.setattr(IntentAgent, "parse", _stub)
    assert IntentAgent.parse is _stub

    response = client.get(SIGNALS_URL, headers=_staff("iso-mon-model"))
    assert response.status_code == 200
    assert len(response.json()["signals"]) == 3
    assert calls == []


# ----------------------------------------------- 10. 跨租户隔离（每信号一条）


def test_every_signal_is_scoped_to_its_own_tenant(
    app: FastAPI, client: TestClient
) -> None:
    mine = "iso-mon-mine"
    theirs = "iso-mon-theirs"
    with app.state.session_factory() as session:
        # 本租户：A 1/2、B 1/1、C 1/2
        _conv(session, mine, "c1")
        for _ in range(2):
            _msg(session, "c1")
        _handoff(session, mine, "c1")
        _task(session, mine, "t1")
        _execs(session, "t1", ToolExecutionStatus.FAILED, 1, "mf")
        _execs(session, "t1", ToolExecutionStatus.SENT, 1, "ms")
        # 另一租户：量大得多，泄漏就会立刻改变分子分母
        for index in range(3):
            their_cid = f"th{index}"
            _conv(session, theirs, their_cid)
            for _ in range(2):
                _msg(session, their_cid)
            _handoff(session, theirs, their_cid, off_intent=(index == 2))
        _task(session, theirs, "t9")
        _execs(session, "t9", ToolExecutionStatus.FAILED, 4, "tf")
        _execs(session, "t9", ToolExecutionStatus.UNKNOWN, 1, "tu")
        session.commit()

    rows = _rows(client, _staff(mine))
    assert _row(rows, NO_EVIDENCE)["numerator"] == 1
    assert _row(rows, NO_EVIDENCE)["denominator"] == 2
    assert _row(rows, HANDOFF)["numerator"] == 1
    assert _row(rows, HANDOFF)["denominator"] == 1
    assert _row(rows, TOOL_FAILURE)["numerator"] == 1
    assert _row(rows, TOOL_FAILURE)["denominator"] == 2

    other_rows = _rows(client, _staff(theirs))
    assert _row(other_rows, NO_EVIDENCE)["denominator"] == 6
    assert _row(other_rows, HANDOFF)["denominator"] == 3
    assert _row(other_rows, TOOL_FAILURE)["denominator"] == 5


# ------------------------------------- 11. 路由注册形态（源码断言）


def test_main_wires_thirteen_routers_and_there_is_no_router_module() -> None:
    source = (REPO_ROOT / "app/main.py").read_text(encoding="utf-8")
    assert source.count("app.include_router") == 13
    assert "    monitor,\n" in source
    assert "app.include_router(monitor.router)" in source
    assert not (REPO_ROOT / "app/api/v1/router.py").exists()


# ------------------------- 12. `_STAFF_ROLES`/`_require_staff` 与 traces 同形


def test_monitor_staff_gate_matches_the_traces_shape() -> None:
    from app.api.v1 import monitor as monitor_module
    from app.api.v1 import traces as traces_module

    assert isinstance(monitor_module._STAFF_ROLES, tuple)
    assert monitor_module._STAFF_ROLES == traces_module._STAFF_ROLES
    assert monitor_module._STAFF_ROLES == ("approver", "support")

    customer = Identity("iso-gate", "u1", frozenset())
    for module in (monitor_module, traces_module):
        with pytest.raises(ForbiddenError) as excinfo:
            module._require_staff(customer)
        assert str(excinfo.value) == "Staff role required."
        assert excinfo.value.status_code == 403
        assert excinfo.value.code == "FORBIDDEN"

    for role in ("approver", "support", "admin"):
        for module in (monitor_module, traces_module):
            assert module._require_staff(Identity("iso-gate", "u1", frozenset({role}))) is None


# ----------------------- 13. 红线可判半条：app/** 无 YAML/配置写路径


def _write_mode(call: ast.Call) -> str | None:
    raw: str | None = None
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        raw = call.args[1].value if isinstance(call.args[1].value, str) else None
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            if isinstance(keyword.value.value, str):
                raw = keyword.value.value
    if raw is None:
        return None
    return raw if any(marker in raw for marker in ("w", "a", "x", "+")) else None


def test_app_source_has_no_yaml_write_paths_and_no_evals_import() -> None:
    dump_offenders: list[str] = []
    write_offenders: list[str] = []
    evals_offenders: list[str] = []

    for path in sorted((REPO_ROOT / "app").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(REPO_ROOT).as_posix()
        if "yaml.safe_dump" in source or "yaml.dump" in source:
            dump_offenders.append(relative)
        tree = ast.parse(source, filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"safe_dump", "dump"}:
                holder = node.value
                if isinstance(holder, ast.Name) and holder.id == "yaml":
                    dump_offenders.append(f"{relative}:{node.lineno}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "open" and _write_mode(node) is not None:
                    target = ast.get_source_segment(source, node) or ""
                    if ".yaml" in target or ".yml" in target:
                        write_offenders.append(f"{relative}:{node.lineno}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "evals":
                        evals_offenders.append(f"{relative}:{node.lineno}")
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] == "evals":
                    evals_offenders.append(f"{relative}:{node.lineno}")

    assert sorted(set(dump_offenders)) == []
    assert sorted(set(write_offenders)) == []
    assert sorted(set(evals_offenders)) == []


# ---------- 14. 复核端点形态与鉴权（§9-7：POST /api/v1/monitor/reviews）


def _review_body(
    signal_id: str = HANDOFF, conclusion: str = "CONFIRMED", **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {"signal_id": signal_id, "conclusion": conclusion}
    body.update(extra)
    return body


def _reviewed_count(app: FastAPI, tenant: str, signal_id: str) -> int:
    with app.state.session_factory() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.tenant_id == tenant,
                    AuditEvent.action == "monitor.signal.reviewed",
                    AuditEvent.resource_type == "monitor_signal",
                    AuditEvent.resource_id == signal_id,
                )
            )
            or 0
        )


def test_review_endpoint_writes_row_for_staff_and_rejects_non_staff(
    app: FastAPI, client: TestClient
) -> None:
    # 端点存在且可调用：staff 三种角色各写一行并回显受理回执；非 staff 一律 403 且不写行。
    for role in ("admin", "approver", "support"):
        tenant = f"iso-mon-rev-staff-{role}"
        response = client.post(
            REVIEW_URL, headers=_staff(tenant, role), json=_review_body()
        )
        assert response.status_code == 200, role
        assert response.json() == {
            "signal_id": HANDOFF,
            "conclusion": "CONFIRMED",
            "window_minutes": None,
        }
        assert _reviewed_count(app, tenant, HANDOFF) == 1, role

    forbidden = client.post(REVIEW_URL, headers=ALICE, json=_review_body())
    assert forbidden.status_code == 403
    error = forbidden.json()["error"]
    assert error["code"] == "FORBIDDEN"
    assert error["message"] == "Staff role required."
    # ALICE 属 demo 租户：403 分支不写任何行。
    assert _reviewed_count(app, "demo", HANDOFF) == 0


def test_review_endpoint_window_minutes_bounds_enforced(
    app: FastAPI, client: TestClient
) -> None:
    # 请求体 window_minutes 越界 → 422（pydantic Field ge=1/le=MAX），且不写行。
    tenant = "iso-mon-rev-window"
    too_small = client.post(
        REVIEW_URL, headers=_staff(tenant), json=_review_body(window_minutes=0)
    )
    assert too_small.status_code == 422
    too_big = client.post(
        REVIEW_URL,
        headers=_staff(tenant),
        json=_review_body(window_minutes=WINDOW_MAX + 1),
    )
    assert too_big.status_code == 422
    assert _reviewed_count(app, tenant, HANDOFF) == 0

    # 边界内可写：最小 1、最大 WINDOW_MAX。
    smallest = client.post(
        REVIEW_URL, headers=_staff(tenant), json=_review_body(window_minutes=WINDOW_MIN)
    )
    assert smallest.status_code == 200
    assert smallest.json()["window_minutes"] == WINDOW_MIN
    largest = client.post(
        REVIEW_URL,
        headers=_staff(tenant),
        json=_review_body(window_minutes=WINDOW_MAX),
    )
    assert largest.status_code == 200
    assert _reviewed_count(app, tenant, HANDOFF) == 2


def test_review_endpoint_rejects_bad_conclusion_and_unknown_signal(
    app: FastAPI, client: TestClient
) -> None:
    # 结论越出封闭词表 / signal_id 不在阈值表：4xx 且绝不写行（§4-7 封闭词表校验，
    # 绝不静默落库）。
    tenant = "iso-mon-rev-bad"
    bad_conclusion = client.post(
        REVIEW_URL, headers=_staff(tenant), json=_review_body(conclusion="MAYBE")
    )
    assert 400 <= bad_conclusion.status_code < 500
    assert bad_conclusion.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _reviewed_count(app, tenant, HANDOFF) == 0

    bad_signal = client.post(
        REVIEW_URL, headers=_staff(tenant), json=_review_body(signal_id="ghost_rate")
    )
    assert 400 <= bad_signal.status_code < 500
    assert bad_signal.json()["error"]["code"] == "VALIDATION_ERROR"


def test_review_endpoint_row_carries_only_the_closed_detail_keys(
    app: FastAPI, client: TestClient
) -> None:
    # reviewed 行契约（§5）：动作/资源/outcome 逐字对，detail 恰两键；混入含 ORD 与
    # 客户文案的会话消息也不得进入复核行的 detail 文本。
    tenant = "iso-mon-rev-detail"
    with app.state.session_factory() as session:
        _conv(session, tenant, "c1")
        session.add(
            ConversationMessage(
                id=f"cm_{uuid.uuid4().hex[:16]}",
                conversation_id="c1",
                role="user",
                intent="CHIT_CHAT",
                content="我的订单 ORD-77881234 还没退款，太气人了",
                created_at=utcnow(),
            )
        )
        session.commit()

    response = client.post(
        REVIEW_URL,
        headers=_staff(tenant),
        json=_review_body(
            signal_id=HANDOFF, conclusion="FALSE_POSITIVE", window_minutes=30
        ),
    )
    assert response.status_code == 200

    with app.state.session_factory() as session:
        event = session.scalars(
            select(AuditEvent).where(
                AuditEvent.tenant_id == tenant,
                AuditEvent.action == "monitor.signal.reviewed",
            )
        ).one()
        assert event.actor_id == "op-1"
        assert event.resource_type == "monitor_signal"
        assert event.resource_id == HANDOFF
        assert event.outcome == "FALSE_POSITIVE"
        assert set(event.detail) == {"signal_id", "window_minutes"}
        assert event.detail == {"signal_id": HANDOFF, "window_minutes": 30}
        text = json.dumps(event.detail, ensure_ascii=False)
        assert "ORD-" not in text
        assert "还没退款" not in text


def test_false_positive_rate_three_states_through_the_real_review_endpoint(
    app: FastAPI, client: TestClient
) -> None:
    # §9-8 用真实复核端点产生 reviewed 行（而非直接种审计行）：0 复核 ⇒ null；
    # 全 CONFIRMED ⇒ 0.0；含 FALSE_POSITIVE ⇒ round(f,4)——三态互异且可观察。
    none_tenant = "iso-mon-rev-tri-none"
    zero_tenant = "iso-mon-rev-tri-zero"
    part_tenant = "iso-mon-rev-tri-part"

    assert (
        client.get(SIGNALS_URL, headers=_staff(none_tenant)).json()["false_positive_rate"]
        is None
    )

    for _ in range(2):
        assert (
            client.post(
                REVIEW_URL,
                headers=_staff(zero_tenant),
                json=_review_body(conclusion="CONFIRMED"),
            ).status_code
            == 200
        )
    zero_read = client.get(SIGNALS_URL, headers=_staff(zero_tenant)).json()[
        "false_positive_rate"
    ]
    assert zero_read == 0.0
    assert zero_read is not None  # 与 null 可分

    assert (
        client.post(
            REVIEW_URL,
            headers=_staff(part_tenant),
            json=_review_body(conclusion="FALSE_POSITIVE"),
        ).status_code
        == 200
    )
    assert (
        client.post(
            REVIEW_URL,
            headers=_staff(part_tenant),
            json=_review_body(conclusion="CONFIRMED"),
        ).status_code
        == 200
    )
    part_read = client.get(SIGNALS_URL, headers=_staff(part_tenant)).json()[
        "false_positive_rate"
    ]
    assert part_read == 0.5


def test_review_endpoint_is_scoped_to_its_own_tenant(
    app: FastAPI, client: TestClient
) -> None:
    # 租户边界：走真实端点，A 租户的复核不改 B 租户的误报率（§9-13 相关）。
    mine = "iso-mon-rev-boundary-mine"
    theirs = "iso-mon-rev-boundary-theirs"
    assert (
        client.post(
            REVIEW_URL,
            headers=_staff(mine),
            json=_review_body(conclusion="FALSE_POSITIVE"),
        ).status_code
        == 200
    )
    assert (
        client.get(SIGNALS_URL, headers=_staff(theirs)).json()["false_positive_rate"]
        is None
    )
    assert (
        client.get(SIGNALS_URL, headers=_staff(mine)).json()["false_positive_rate"] == 1.0
    )


# ---------- 15. 两个出口函数都不得直接触碰审计内部（§9 纪律，扩展到双路由函数）


def test_both_monitor_route_functions_do_not_touch_audit_internals() -> None:
    import app.api.v1.monitor as monitor_module

    # 不弱化既有整模块断言：这里逐函数钉，覆盖读端点与复核端点两个路由。
    for route in (monitor_module.list_signals, monitor_module.review_signal):
        source = inspect.getsource(route)
        for forbidden in (
            "AuditRepository",
            "AuditEvent",
            "app.models.audit",
            "app.repositories",
        ):
            assert forbidden not in source, (
                f"出口层路由函数 {route.__name__} 不得触碰审计内部：{forbidden!r}"
            )
