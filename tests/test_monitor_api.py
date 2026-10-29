"""R11 T4：只读通知出口 `GET /api/v1/monitor/signals` 的行为与红线用例。

口径来源：`.qoder/rules/online-signals-and-notification.md` §4-1/§4-2/§4-3/§4-4、
§2 通用规则、§2-B 裁定 11（零分母在**出口层**映射为 `null`）、§6 红线、
§9 第 7/10/11/13/14 条。断言一律对着口径的字面契约，不对着实现自证。

本文件同时钉住 T4 的边界：误报率恒 `null`（生产者属 T5）、不读审计台账、
零模型调用、`app/main.py` 恰 13 行 `include_router`、不新建 `router.py`。
"""

from __future__ import annotations

import ast
import inspect
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

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


# ------------------------ 8. 误报率恒 null，且出口不读审计台账


def test_false_positive_rate_is_null_and_audit_rows_do_not_move_it(
    app: FastAPI, client: TestClient
) -> None:
    tenant = "iso-mon-fpr"
    with app.state.session_factory() as session:
        for index, outcome in enumerate(("FALSE_POSITIVE", "CONFIRMED", "FALSE_POSITIVE")):
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
                    created_at=utcnow(),
                )
            )
        session.commit()

    headers = _staff(tenant)
    first = client.get(SIGNALS_URL, headers=headers).json()
    assert first["false_positive_rate"] is None
    # 复核行（T5 的生产者）存在与否不得影响 T4 的读数。
    second = client.get(SIGNALS_URL, headers=headers).json()
    assert second == first
    assert second["false_positive_rate"] is None

    import app.api.v1.monitor as monitor_module

    source = inspect.getsource(monitor_module)
    for forbidden in ("AuditRepository", "app.models.audit", "app.repositories"):
        assert forbidden not in source, f"出口层不得触碰审计台账：{forbidden!r}"


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
