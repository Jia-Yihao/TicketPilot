"""F-8: 档案摘要只有一个出口 —— 移交包。

客户自己写下的档案摘要一直被存着、脱敏着、也能导出，可除了客户自己看，
没有任何人读它。这一轮给它一个出口：坐席接管时看到的**移交包**。

这些用例钉住 `app/services/handoff` 的口径：

* 摘要进移交包，且**只**进移交包 —— 不进客户回复、不进追踪、不进答案路径；
* 出包时再脱敏一次，然后按移交要点的同一个上限截断；
* 没有可读的内容时，包里**什么都不写** —— 不写 ``null``、不写空串、不写 0；
* 读它不写审计、不建行、不碰档案的更新时间；
* 换一个用户、换一个租户都读不到别人的摘要；
* 既有那些键一个都不动。
"""

from __future__ import annotations

import ast
import importlib
import json
import pathlib
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.core.config import Settings
from app.core.policy import PolicyEngine
from app.core.security import Identity
from app.models.audit import AuditEvent
from app.models.conversation import ConversationMessage
from app.models.memory import CustomerProfile
from app.models.task import Task
from tests.conftest import ALICE

ROOT = pathlib.Path(__file__).resolve().parents[1]

SEAT = {"X-Tenant-Id": "demo", "X-User-Id": "agent-1", "X-Roles": "support"}
ACME_SEAT = {"X-Tenant-Id": "acme", "X-User-Id": "agent-9", "X-Roles": "support"}
OTHER_USER = {"X-Tenant-Id": "demo", "X-User-Id": "bob"}
OTHER_TENANT = {"X-Tenant-Id": "acme", "X-User-Id": "alice"}

SUMMARY = "上周问过发票抬头"
SOURCE = "customer_profiles.history_summary"
LIMIT = PolicyEngine.from_settings(Settings(environment="test")).escalation.digest_max_chars

# 移交包本轮之前就有的键：一个都不许动。
EXISTING_BUNDLE_KEYS = frozenset(
    {
        "conversation_id",
        "tenant_id",
        "user_id",
        "channel",
        "reason",
        "signal_id",
        "priority",
        "queue",
        "transcript_digest",
        "transcript_ref",
        "intent_history",
        "slots",
        "attempted_actions",
        "attempted_candidates",
        "pending_task_id",
        "sentiment",
        "policy_refs",
        "suggested_next_step",
        "created_at",
    }
)
NEW_BUNDLE_KEYS = frozenset(
    {"history_summary", "history_summary_source", "history_summary_max_chars"}
)


# --------------------------------------------------------------------- 辅助
def _message(
    client: TestClient,
    headers: dict[str, str],
    text: str,
    **extra: object,
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/support/messages",
        headers=headers,
        json={"message": text, **extra},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _case(
    client: TestClient,
    event_id: str,
    headers: dict[str, str] = SEAT,
) -> dict[str, Any]:
    response = client.get(f"/api/v1/handoffs/{event_id}", headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _bundle(
    client: TestClient,
    headers: dict[str, str],
    seat: dict[str, str] = SEAT,
) -> dict[str, Any]:
    body = _message(client, headers, "转人工")
    event_id = body["handoff"]["event_id"]
    return dict(_case(client, event_id, seat)["bundle"])


def _write_profile(client: TestClient, **changes: Any) -> None:
    response = client.put("/api/v1/memory", headers=ALICE, json=changes)
    assert response.status_code == 200, response.text


def _write_raw_summary(
    app: FastAPI,
    tenant_id: str,
    user_id: str,
    summary: str,
) -> None:
    """绕开写入路径自带的脱敏，证明**读出这一侧**又脱了一次。"""
    with app.state.session_factory() as session:
        session.execute(
            update(CustomerProfile)
            .where(
                CustomerProfile.tenant_id == tenant_id,
                CustomerProfile.user_id == user_id,
            )
            .values(history_summary=summary)
        )
        session.commit()


def _profile_row(app: FastAPI, tenant_id: str, user_id: str) -> CustomerProfile | None:
    with app.state.session_factory() as session:
        row = session.scalars(
            select(CustomerProfile).where(
                CustomerProfile.tenant_id == tenant_id,
                CustomerProfile.user_id == user_id,
            )
        ).one_or_none()
        if row is None:
            return None
        session.refresh(row)
        return row


def _audit_actions(app: FastAPI, like: str) -> list[str]:
    with app.state.session_factory() as session:
        return [
            str(action)
            for action in session.scalars(
                select(AuditEvent.action).where(AuditEvent.action.like(like))
            ).all()
        ]


def _profile_count(app: FastAPI) -> int:
    with app.state.session_factory() as session:
        return int(session.scalar(select(func.count(CustomerProfile.id))) or 0)


def _reader() -> Any:
    """取读出口本身；还没实现时给一句人话，而不是一个导入错误。"""
    module = importlib.import_module("app.services.memory")
    function = getattr(module, "load_history_summary", None)
    assert callable(function), (
        "读出口还不存在：记忆服务里没有 load_history_summary"
    )
    return function


def _reads_the_column(path: pathlib.Path) -> bool:
    """模块的**代码**（不是说明文字）是否碰了这一列。

    用 AST 判断：注释与文档字符串里提到列名不算数。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "history_summary":
            return True
        if isinstance(node, ast.Name) and node.id == "history_summary":
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value == "history_summary"
            and id(node) not in docstrings
        ):
            return True
    return False


# ------------------------------------------------------- 1. 摘要进得了移交包
def test_a_profile_summary_reaches_the_handoff_bundle(client: TestClient) -> None:
    _write_profile(client, history_summary=SUMMARY)

    bundle = _bundle(client, ALICE)

    assert bundle["history_summary"] == SUMMARY
    assert bundle["history_summary_source"] == SOURCE
    assert bundle["history_summary_max_chars"] == LIMIT


# --------------------------------------------------------- 2. 出包时再脱敏一次
def test_the_bundle_masks_the_summary_again(app: FastAPI, client: TestClient) -> None:
    raw = (
        "手机 13800138000 邮箱 alice@example.com 身份证 110101199003071234 "
        "卡号 6222021234567890123"
    )
    _write_profile(client, history_summary="占位")
    _write_raw_summary(app, "demo", "alice", raw)

    bundle = _bundle(client, ALICE)
    summary = bundle["history_summary"]

    for secret in (
        "13800138000",
        "alice@example.com",
        "110101199003071234",
        "6222021234567890123",
    ):
        assert secret not in summary
        assert secret not in json.dumps(bundle, ensure_ascii=False)
    assert "***" in summary


# --------------------------------------------------------------- 3. 超长要截断
def test_an_overlong_summary_is_clipped_to_the_digest_limit(
    app: FastAPI, client: TestClient
) -> None:
    _write_profile(client, history_summary="占位")
    _write_raw_summary(app, "demo", "alice", "客" * (LIMIT + 120))

    bundle = _bundle(client, ALICE)
    summary = bundle["history_summary"]

    assert len(summary) == LIMIT
    assert summary.endswith("…")
    assert bundle["history_summary_max_chars"] == LIMIT


# ------------------------------------------------------------ 4. 不超长就不动
def test_a_short_summary_is_carried_verbatim(client: TestClient) -> None:
    _write_profile(client, history_summary=SUMMARY)

    bundle = _bundle(client, ALICE)
    summary = bundle["history_summary"]

    assert summary == SUMMARY
    assert len(summary) == len(SUMMARY)
    assert "…" not in summary
    assert bundle["history_summary_max_chars"] == LIMIT


# ------------------------------------------------------- 5. 没有档案就不占位
def test_a_customer_without_a_profile_leaves_the_three_keys_out(
    app: FastAPI, client: TestClient
) -> None:
    bundle = _bundle(client, ALICE)

    for key in NEW_BUNDLE_KEYS:
        assert key not in bundle
    # 读不读都不能建行。
    assert _profile_count(app) == 0


# ----------------------------------------------------------- 6. 空白也不占位
def test_a_blank_summary_leaves_the_three_keys_out(client: TestClient) -> None:
    _write_profile(client, history_summary="   ")

    bundle = _bundle(client, ALICE)

    for key in NEW_BUNDLE_KEYS:
        assert key not in bundle


# -------------------------------------------- 队列行摘要不带档案摘要
def test_the_queue_row_does_not_carry_the_profile_summary(
    client: TestClient,
) -> None:
    _write_profile(client, history_summary=SUMMARY)
    body = _message(client, ALICE, "转人工")

    queue = client.get("/api/v1/handoffs", headers=SEAT)
    assert queue.status_code == 200, queue.text
    row = queue.json()["items"][0]

    assert row["event_id"] == body["handoff"]["event_id"]
    assert "history_summary" not in row
    assert SUMMARY not in json.dumps(row, ensure_ascii=False)


# ------------------------------- 7. 不进回复、不进追踪、不进答案路径（模型输入面）
def test_the_summary_never_reaches_the_reply_the_trace_or_the_answer_path(
    app: FastAPI, client: TestClient
) -> None:
    _write_profile(client, history_summary=SUMMARY)

    body = _message(client, ALICE, "转人工")

    # 客户看到的：一个字都不能多。
    assert SUMMARY not in body["reply"]
    assert SUMMARY not in json.dumps(body, ensure_ascii=False)
    assert SUMMARY not in json.dumps(body.get("suggestions", []), ensure_ascii=False)

    # 追踪里不能留痕。
    trace = client.get(f"/api/v1/traces/{body['trace_id']}", headers=ALICE)
    assert trace.status_code == 200, trace.text
    assert SUMMARY not in trace.text

    # 任务上下文里不能留痕。
    with app.state.session_factory() as session:
        for message in session.scalars(select(ConversationMessage)).all():
            assert SUMMARY not in (message.content or "")
            assert SUMMARY not in json.dumps(message.payload or {}, ensure_ascii=False)
        for task in session.scalars(select(Task)).all():
            assert SUMMARY not in (task.request or "")
            assert SUMMARY not in json.dumps(task.context or {}, ensure_ascii=False)

    # 答案路径（模型输入面）不读这一列：源码里连引用都不许有。
    from app.agent.personalization import ProfileSnapshot

    assert not hasattr(ProfileSnapshot, "history_summary")
    assert not _reads_the_column(ROOT / "app" / "agent" / "personalization.py")
    assert not _reads_the_column(ROOT / "app" / "agent" / "pydantic_agent.py")


# ------------------------------------------------------------ 8. 不留痕、不写库
def test_reading_the_summary_leaves_no_audit_row_and_no_profile_write(
    app: FastAPI, client: TestClient
) -> None:
    _write_profile(client, history_summary=SUMMARY)
    before_actions = _audit_actions(app, "memory.%")
    before_updated = _profile_row(app, "demo", "alice")
    assert before_updated is not None
    before_timestamp = before_updated.updated_at

    bundle = _bundle(client, ALICE)
    assert bundle["history_summary"] == SUMMARY

    # 转人工本身有审计（handoff.escalate），档案那类一条都不许多。
    assert _audit_actions(app, "memory.%") == before_actions
    assert "handoff.escalate" in _audit_actions(app, "handoff.%")
    after = _profile_row(app, "demo", "alice")
    assert after is not None
    assert after.updated_at == before_timestamp


# ------------------------------------------------------------ 9. 租户与用户边界
def test_another_user_or_another_tenant_cannot_read_the_summary(
    client: TestClient,
) -> None:
    _write_profile(client, history_summary=SUMMARY)

    other_user_bundle = _bundle(client, OTHER_USER)
    other_tenant_bundle = _bundle(client, OTHER_TENANT, ACME_SEAT)

    for bundle in (other_user_bundle, other_tenant_bundle):
        for key in NEW_BUNDLE_KEYS:
            assert key not in bundle
        assert SUMMARY not in json.dumps(bundle, ensure_ascii=False)


# ------------------------------------------------------------ 10. 既有键不动
def test_the_existing_bundle_keys_are_unchanged(client: TestClient) -> None:
    without_profile = _bundle(client, ALICE)
    assert set(without_profile) == EXISTING_BUNDLE_KEYS

    _write_profile(client, history_summary=SUMMARY)
    with_profile = _bundle(client, ALICE)

    assert set(with_profile) == EXISTING_BUNDLE_KEYS | NEW_BUNDLE_KEYS
    for key in (
        "tenant_id",
        "user_id",
        "reason",
        "signal_id",
        "priority",
        "queue",
        "suggested_next_step",
    ):
        assert with_profile[key] == without_profile[key]


# ------------------------------------------------------------- 读取出口本身
def test_the_reader_returns_the_masked_summary_for_its_owner(
    app: FastAPI, client: TestClient
) -> None:
    load_history_summary = _reader()

    _write_profile(client, history_summary=SUMMARY)
    identity = Identity(tenant_id="demo", user_id="alice", roles=frozenset())

    with app.state.session_factory() as session:
        assert load_history_summary(session, identity) == SUMMARY


def test_the_reader_masks_before_it_returns(app: FastAPI, client: TestClient) -> None:
    load_history_summary = _reader()

    _write_profile(client, history_summary="占位")
    _write_raw_summary(app, "demo", "alice", "手机 13800138000 邮箱 alice@example.com")
    identity = Identity(tenant_id="demo", user_id="alice", roles=frozenset())

    with app.state.session_factory() as session:
        value = load_history_summary(session, identity)

    assert value is not None
    assert "13800138000" not in value
    assert "alice@example.com" not in value
    assert "***" in value


def test_the_reader_returns_none_when_there_is_nothing_to_read(
    app: FastAPI, client: TestClient
) -> None:
    load_history_summary = _reader()

    identity = Identity(tenant_id="demo", user_id="alice", roles=frozenset())
    with app.state.session_factory() as session:
        assert load_history_summary(session, identity) is None

    _write_profile(client, history_summary="   ")
    with app.state.session_factory() as session:
        assert load_history_summary(session, identity) is None


def test_the_reader_creates_no_row_and_writes_no_audit(
    app: FastAPI, client: TestClient
) -> None:
    load_history_summary = _reader()

    identity = Identity(tenant_id="demo", user_id="alice", roles=frozenset())
    with app.state.session_factory() as session:
        assert load_history_summary(session, identity) is None

    assert _profile_count(app) == 0
    assert _audit_actions(app, "memory.%") == []
