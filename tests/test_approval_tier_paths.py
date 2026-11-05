"""B-3 第二轮（B3-ii）：阈值在写路径生效、自动执行路径与审批路径同强度。

口径来源：`app/core/approval_tiers.yaml`
- §4.2 自动执行路径八条：不起审批行、不进 WAITING_APPROVAL、仍写幂等台账、
  调同一个权威执行体、result 顶层 ``approval_path`` 封闭取值、任务事件
  ``refund.auto_executed``、审计、回执措辞不复用「已审批通过」。
- §4.2.1 一个执行体：``execute`` 是审批绑定包装器，``execute_auto`` 是台账
  绑定入口；六项权威校验两路径同强度；绑定校验都保留（审批行 digest /
  台账 request_digest 重算）。
- §4.3 两条路径可区分：区分靠 approvals 表有没有那一行；``approval_path``
  落点在 result 顶层与任务事件。
- §3 阈值只答「要不要人批」；§8 第 12 条：资格不过 / 并发冲突分支两条路径
  产生同样的台账状态、审计动作与回执文案。
- §5.4 ``approval_path`` 不进载荷、不进摘要。

每条测试头部注释标明它钉住的条款。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.errors import ApprovalRequiredError
from app.core.security import Identity
from app.models.approval import Approval
from app.models.audit import AuditEvent
from app.models.commerce import Order, Refund
from app.models.enums import (
    ApprovalStatus,
    OrderStatus,
    RefundReason,
    RefundStatus,
    RefundType,
    ToolExecutionStatus,
    VerificationStatus,
)
from app.models.task import TaskEvent
from app.models.tool_execution import ToolExecution
from app.services.approvals import canonical_digest
from app.services.refunds import refund_reason_label, refund_type_label
from app.tools.base import ToolContext
from tests.conftest import ALICE, REVIEWER

AMOUNT_CENTS = 12900
# 高于 12900 的阈值：129 元这笔试的是自动路径。取一个不会偶然出现在
# 十六进制 id / 摘要里的数字，便于做「客户可见文本检索不到阈值」的扫描。
AUTO_THRESHOLD = 99999999
# 默认档位表 T1 的上界；同样只用于泄漏扫描。
DEFAULT_TIER_MAX_CENTS = 5000000

AUTO_RECEIPT_PHRASE = "已按规则自动处理"
APPROVAL_RECEIPT_PHRASE = "已审批通过"

# 默认阈值 0（恒人批）下 check_refund_eligibility 只读回执的**逐字**基线：
# 本轮之前该串由 builtin.py 的 receipt 模板固定生成（口径 §8.1 / §4.2.1 不变量 5），
# 回执改造后 requires_approval 为真时输出必须与此串逐字相等，不是「包含」。
DEFAULT_ELIGIBILITY_RECEIPT = "按当前政策，该订单最多可退 129.00 CNY，实际退款需要人工审批。"


def _eligibility_receipt_messages(app: FastAPI, task_id: str) -> list[str]:
    """tool.call_completed 事件里 check_refund_eligibility 的 message（客户可见）。"""
    messages: list[str] = []
    for event in _task_events(app, task_id):
        if event.event_type != "tool.call_completed":
            continue
        payload = dict(event.payload or {})
        if payload.get("tool") == "check_refund_eligibility":
            messages.append(str(payload.get("message", "")))
    return messages


def _request_refund(client: TestClient, text: str | None = None) -> dict[str, Any]:
    response = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={
            "request": text
            or "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题"
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approve(client: TestClient, task_id: str) -> Any:
    return client.post(
        f"/api/v1/tasks/{task_id}/approve",
        headers=REVIEWER,
        json={"decision": "APPROVE"},
    )


def _snapshot(client: TestClient, task_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/tasks/{task_id}", headers=ALICE)
    assert response.status_code == 200, response.text
    return response.json()


def _task_events(app: FastAPI, task_id: str) -> list[TaskEvent]:
    with app.state.session_factory() as session:
        return list(
            session.scalars(
                select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(
                    TaskEvent.created_at, TaskEvent.id
                )
            )
        )


def _event_payload(app: FastAPI, task_id: str, event_type: str) -> dict[str, Any] | None:
    for event in _task_events(app, task_id):
        if event.event_type == event_type:
            return dict(event.payload or {})
    return None


def _approval_rows(app: FastAPI, task_id: str) -> list[Approval]:
    with app.state.session_factory() as session:
        return list(session.scalars(select(Approval).where(Approval.task_id == task_id)))


def _ledger_row(app: FastAPI, task_id: str) -> ToolExecution:
    with app.state.session_factory() as session:
        row = session.scalar(
            select(ToolExecution).where(ToolExecution.task_id == task_id)
        )
        assert row is not None
        return row


def _refund_rows(app: FastAPI) -> list[Refund]:
    with app.state.session_factory() as session:
        return list(session.scalars(select(Refund)))


def _audit_rows(app: FastAPI, action: str) -> list[AuditEvent]:
    with app.state.session_factory() as session:
        return list(session.scalars(select(AuditEvent).where(AuditEvent.action == action)))


def _order_state(app: FastAPI) -> tuple[str, int, int]:
    with app.state.session_factory() as session:
        order = session.get(Order, "ORD-DEMO-1001")
        assert order is not None
        return order.status.value, order.refundable_cents, order.version


def _set_order_status(app: FastAPI, status: OrderStatus) -> None:
    with app.state.session_factory() as session:
        order = session.get(Order, "ORD-DEMO-1001")
        assert order is not None
        order.status = status
        session.commit()


def _set_approval_status(app: FastAPI, approval_id: str, status: ApprovalStatus) -> None:
    with app.state.session_factory() as session:
        approval = session.get(Approval, approval_id)
        assert approval is not None
        approval.status = status
        session.commit()


def _execute_auto(app: FastAPI, task_id: str):  # noqa: ANN202 - RefundOutcome
    from app.services.refunds import RefundExecutionService

    with app.state.session_factory() as session:
        services = app.state.runtime._services(session)
        refunds: RefundExecutionService = services.refunds
        return refunds.execute_auto(task_id)


def _reconcile_task(app: FastAPI, task_id: str):  # noqa: ANN202 - RefundOutcome
    from app.services.refunds import RefundExecutionService

    with app.state.session_factory() as session:
        services = app.state.runtime._services(session)
        refunds: RefundExecutionService = services.refunds
        return refunds.reconcile_task(task_id)


# --------------------------------------------------------------------------
# §4.2 第 1 条 + 默认不变：阈值不满足（默认阈值 0）时审批路径读数逐字不变，
# 但 requires_approval 读的是资格结果里的真值，且 result 顶层有 approval_path。
# --------------------------------------------------------------------------


def test_default_threshold_keeps_the_approval_path_and_stamps_the_real_flag(
    client: TestClient, app: FastAPI
) -> None:
    created = _request_refund(client)
    assert created["status"] == "WAITING_APPROVAL"
    result = created["result"]
    assert result["pending_action"]["requires_approval"] is True
    assert result["approval_path"] == "APPROVAL"

    event = _event_payload(app, created["task_id"], "approval.requested")
    assert event is not None
    assert event["approval_path"] == "APPROVAL"

    approved = _approve(client, created["task_id"])
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "SUCCEEDED"
    assert body["result"]["outcome"] == "VERIFIED_SUCCESS"
    assert body["result"]["approval_path"] == "APPROVAL"
    summary = body["result"]["summary"]
    assert "已由权威退款记录确认" in summary
    # 审批路径不得出现自动路径措辞。
    assert AUTO_RECEIPT_PHRASE not in summary
    assert len(_approval_rows(app, created["task_id"])) == 1


# --------------------------------------------------------------------------
# §4.2 八条（阈值满足自动执行）：对应简报测试第 1、4、5、7、8 条。
# --------------------------------------------------------------------------


def test_below_threshold_auto_executes_without_an_approval_row(
    app_factory: Callable[..., FastAPI]
) -> None:
    app = app_factory(refund_approval_threshold_cents=AUTO_THRESHOLD)
    client = TestClient(app)

    created = _request_refund(client)
    task_id = created["task_id"]
    assert created["status"] == "SUCCEEDED"
    result = created["result"]
    assert result["approval_path"] == "AUTO"
    assert result["outcome"] == "VERIFIED_SUCCESS"

    # 1) 不起审批行。
    assert created["pending_approval"] is None
    assert _approval_rows(app, task_id) == []
    # 2) 不进 WAITING_APPROVAL（上面 status == SUCCEEDED 已证）。
    # 3) 仍写幂等台账：同形状、同摘要输入，approval_path 不进载荷（§5.4）。
    ledger = _ledger_row(app, task_id)
    payload = dict(ledger.request_payload or {})
    assert "approval_path" not in payload
    assert set(payload) == {
        "action",
        "order_id",
        "amount_cents",
        "currency",
        "resource_version",
        "policy_version",
        "reason",
        "refund_type",
        "approval_tier",
    }
    assert canonical_digest(payload) == ledger.request_digest
    assert ledger.status is ToolExecutionStatus.RECONCILED
    # 权威执行体的效果：退款行 + 订单余额。
    assert len(_refund_rows(app)) == 1
    status, refundable, _version = _order_state(app)
    assert status == "DELIVERED"
    assert refundable == 0
    # 4) 回执措辞：给出自动处理类措辞，不复用「已审批通过」。
    summary = result["summary"]
    assert AUTO_RECEIPT_PHRASE in summary
    assert "已由权威退款记录确认" in summary
    assert APPROVAL_RECEIPT_PHRASE not in summary
    # 5) 任务事件 refund.auto_executed。
    event = _event_payload(app, task_id, "refund.auto_executed")
    assert event is not None
    assert event["approval_path"] == "AUTO"
    assert event["order_id"] == "ORD-DEMO-1001"
    assert event["amount_cents"] == AMOUNT_CENTS
    # 6) 审计：detail 含 task_id/order_id/amount_cents/tier_id。
    audits = _audit_rows(app, "refund.auto_executed")
    assert len(audits) == 1
    detail = dict(audits[0].detail or {})
    assert set(detail) == {"task_id", "order_id", "amount_cents", "tier_id"}
    assert detail["task_id"] == task_id
    assert detail["order_id"] == "ORD-DEMO-1001"
    assert detail["amount_cents"] == AMOUNT_CENTS
    assert detail["tier_id"] == "T1"


# --------------------------------------------------------------------------
# §3 / §4.2：阈值两侧行为相反——同一金额只由阈值决定走哪条路。
# --------------------------------------------------------------------------


def test_the_threshold_flips_the_write_path_at_the_boundary(
    app_factory: Callable[..., FastAPI]
) -> None:
    # 12900 > 12899：要人批。
    app_over = app_factory(refund_approval_threshold_cents=AMOUNT_CENTS - 1)
    client_over = TestClient(app_over)
    created = _request_refund(client_over)
    assert created["status"] == "WAITING_APPROVAL"
    assert created["result"]["approval_path"] == "APPROVAL"

    # 12900 > 12900 为假：不要人批 → 自动执行。
    app_at = app_factory(refund_approval_threshold_cents=AMOUNT_CENTS)
    client_at = TestClient(app_at)
    created_at = _request_refund(client_at)
    assert created_at["status"] == "SUCCEEDED"
    assert created_at["result"]["approval_path"] == "AUTO"
    assert len(_refund_rows(app_at)) == 1


# --------------------------------------------------------------------------
# §4.2 第 6 条：客户可见文本（任务快照 + 任务事件）检索不到档位内部口径。
# --------------------------------------------------------------------------


def test_customer_visible_text_carries_no_tier_internals(
    app_factory: Callable[..., FastAPI]
) -> None:
    app = app_factory(refund_approval_threshold_cents=AUTO_THRESHOLD)
    client = TestClient(app)
    created = _request_refund(client)
    task_id = created["task_id"]

    visible = [json.dumps(created, ensure_ascii=False)]
    visible.append(json.dumps(_snapshot(client, task_id), ensure_ascii=False))
    for event in _task_events(app, task_id):
        visible.append(json.dumps(dict(event.payload or {}), ensure_ascii=False))
    blob = "\n".join(visible)

    for token in (
        "tier_id",
        "required_approvals",
        "max_cents",
        "approver",
        "roles",
        str(DEFAULT_TIER_MAX_CENTS),
        str(AUTO_THRESHOLD),
    ):
        assert token not in blob, f"客户可见文本泄漏内部口径：{token}"


# --------------------------------------------------------------------------
# §4.2.1 不变量（同强度）+ §8 第 12 条：资格不过分支两条路径产生
# 同样的台账状态、审计动作与回执文案。
# --------------------------------------------------------------------------


def test_auto_entry_rechecks_eligibility_at_the_same_strength(
    app_factory: Callable[..., FastAPI]
) -> None:
    app = app_factory()
    client = TestClient(app)

    # 审批路径：任务建好后订单被取消 → approve 时执行前复核资格不过。
    first = _request_refund(client)
    _set_order_status(app, OrderStatus.CANCELLED)
    approved = _approve(client, first["task_id"])
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "FAILED"
    assert body["result"]["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert body["result"]["reason_code"] == "ELIGIBILITY_CHANGED"
    approval_path_summary = body["result"]["summary"]
    ledger_a = _ledger_row(app, first["task_id"])
    assert ledger_a.status is ToolExecutionStatus.CANCELLED
    assert ledger_a.error_code == "ELIGIBILITY_CHANGED"
    assert len(_refund_rows(app)) == 0
    # 资格不过分支不写 refund.invalidated 审计（今天审批路径就没有；
    # §8.12 要求自动路径同形，这里钉住两条路径都是零）。
    assert _audit_rows(app, "refund.invalidated") == []

    # 自动入口镜像：恢复订单 → 再建一个任务 → 订单再取消 → execute_auto。
    _set_order_status(app, OrderStatus.DELIVERED)
    second = _request_refund(client)
    approval_id = second["pending_approval"]["approval_id"]
    # 模拟「自动路径没有审批行」：把审批行置为非活动，自动入口不读它。
    _set_approval_status(app, approval_id, ApprovalStatus.CANCELLED)
    _set_order_status(app, OrderStatus.CANCELLED)

    outcome = _execute_auto(app, second["task_id"])
    assert outcome.summary == approval_path_summary
    assert outcome.reason_code == "ELIGIBILITY_CHANGED"
    ledger_b = _ledger_row(app, second["task_id"])
    assert ledger_b.status is ToolExecutionStatus.CANCELLED
    assert ledger_b.error_code == "ELIGIBILITY_CHANGED"
    assert len(_refund_rows(app)) == 0
    assert _audit_rows(app, "refund.invalidated") == []
    # 自动入口不失效审批行：它根本不经审批行。
    with app.state.session_factory() as session:
        approval = session.get(Approval, approval_id)
        assert approval is not None
        assert approval.status is ApprovalStatus.CANCELLED


# --------------------------------------------------------------------------
# §4.2.1：自动入口的绑定校验 = 台账 request_digest 重算比对 + 版本比对，
# 篡改与版本漂移都要现形（强度对照审批行的 verify_binding）。
# --------------------------------------------------------------------------


def test_tampered_ledger_payload_is_refused_by_the_auto_entry(
    app_factory: Callable[..., FastAPI]
) -> None:
    app = app_factory()
    client = TestClient(app)
    created = _request_refund(client)
    task_id = created["task_id"]

    # 只改载荷、不改摘要：重算比对必须检出。
    with app.state.session_factory() as session:
        ledger = session.scalar(select(ToolExecution).where(ToolExecution.task_id == task_id))
        assert ledger is not None
        tampered = dict(ledger.request_payload or {})
        tampered["amount_cents"] = 1
        ledger.request_payload = tampered
        session.commit()

    outcome = _execute_auto(app, task_id)
    assert outcome.reason_code == "EXECUTION_DIGEST_MISMATCH"
    assert outcome.summary == "执行台账与当前订单或摘要不一致，本次未执行退款；已转人工复核。"
    ledger = _ledger_row(app, task_id)
    assert ledger.status is ToolExecutionStatus.CANCELLED
    assert ledger.error_code == "EXECUTION_DIGEST_MISMATCH"
    assert len(_refund_rows(app)) == 0
    audits = _audit_rows(app, "refund.invalidated")
    assert any(
        (row.detail or {}).get("reason") == "EXECUTION_DIGEST_MISMATCH" for row in audits
    )


def test_order_version_drift_is_refused_by_the_auto_entry(
    app_factory: Callable[..., FastAPI]
) -> None:
    app = app_factory()
    client = TestClient(app)
    created = _request_refund(client)
    task_id = created["task_id"]

    with app.state.session_factory() as session:
        order = session.get(Order, "ORD-DEMO-1001")
        assert order is not None
        order.version += 1
        session.commit()

    outcome = _execute_auto(app, task_id)
    assert outcome.reason_code == "RESOURCE_VERSION_CHANGED"
    assert outcome.summary == "执行台账与当前订单或摘要不一致，本次未执行退款；已转人工复核。"
    ledger = _ledger_row(app, task_id)
    assert ledger.status is ToolExecutionStatus.CANCELLED
    assert ledger.error_code == "RESOURCE_VERSION_CHANGED"
    assert len(_refund_rows(app)) == 0


# --------------------------------------------------------------------------
# §4.2 第 3 条：自动路径不经 ToolGateway 发起执行——工具闸门原样存在，
# request_refund 的 requires_approval 声明不得松动。
# --------------------------------------------------------------------------


def test_the_tool_gate_still_demands_approval(app: FastAPI) -> None:
    with app.state.session_factory() as session:
        services = app.state.runtime._services(session)
        manifest = services.gateway.registry.require("request_refund").manifest
        assert manifest.requires_approval is True
        with pytest.raises(ApprovalRequiredError):
            services.gateway.call(
                "request_refund",
                ToolContext(
                    identity=Identity("demo", "alice", frozenset()),
                    task_id="task-without-approval",
                ),
                {},
            )


# --------------------------------------------------------------------------
# §8 第 12 条：并发冲突分支（同订单已有他笔退款）两条路径同台账状态、
# 同审计动作（零）、同回执文案。
# --------------------------------------------------------------------------


def test_concurrent_refund_conflict_lands_identically_on_both_paths(
    app_factory: Callable[..., FastAPI]
) -> None:
    app = app_factory()
    client = TestClient(app)

    def _inject_foreign() -> None:
        with app.state.session_factory() as session:
            session.add(
                Refund(
                    id="rfd_foreign",
                    order_id="ORD-DEMO-1001",
                    tenant_id="demo",
                    user_id="alice",
                    amount_cents=AMOUNT_CENTS,
                    status=RefundStatus.SUCCEEDED,
                    external_refund_id="ext_foreign",
                    idempotency_key="refund:other-task",
                )
            )
            session.commit()

    def _remove_foreign() -> None:
        with app.state.session_factory() as session:
            row = session.get(Refund, "rfd_foreign")
            assert row is not None
            session.delete(row)
            session.commit()

    first = _request_refund(client)
    _inject_foreign()
    approved = _approve(client, first["task_id"])
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["result"]["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert body["result"]["reason_code"] == "ORDER_REFUND_IN_PROGRESS"
    approval_path_summary = body["result"]["summary"]
    ledger_a = _ledger_row(app, first["task_id"])
    assert ledger_a.status is ToolExecutionStatus.CANCELLED
    assert ledger_a.error_code == "ORDER_REFUND_IN_PROGRESS"
    assert ledger_a.error_message == "another refund exists for this order: rfd_foreign"
    assert _audit_rows(app, "refund.invalidated") == []

    # 自动入口镜像：先撤掉挡板让第二个任务能建起来，执行前再放回。
    _remove_foreign()
    second = _request_refund(client)
    _inject_foreign()
    _set_approval_status(
        app, second["pending_approval"]["approval_id"], ApprovalStatus.CANCELLED
    )

    outcome = _execute_auto(app, second["task_id"])
    assert outcome.summary == approval_path_summary
    assert outcome.reason_code == "ORDER_REFUND_IN_PROGRESS"
    ledger_b = _ledger_row(app, second["task_id"])
    assert ledger_b.status is ToolExecutionStatus.CANCELLED
    assert ledger_b.error_code == "ORDER_REFUND_IN_PROGRESS"
    assert ledger_b.error_message == "another refund exists for this order: rfd_foreign"
    # 两路径都没执行退款（只有那颗挡板行）。
    assert len(_refund_rows(app)) == 1
    # 该分支两路径都不写失效审计。
    assert _audit_rows(app, "refund.invalidated") == []


# --------------------------------------------------------------------------
# §4.3 / §3.1（返工第 2 轮）：check_refund_eligibility 的只读回执阈值感知。
# 自动路径上，同任务时间线的客户可见文本（tool.call_completed 的 message
# 与最终 result.summary）**不得**同时出现「需要人工审批」与「已按规则自动处理」；
# 「实际退款需要人工审批」这句断言在 requires_approval 为假时不允许出现。
# --------------------------------------------------------------------------


def test_eligibility_receipt_does_not_claim_manual_approval_on_auto_path(
    app_factory: Callable[..., FastAPI]
) -> None:
    app = app_factory(refund_approval_threshold_cents=AUTO_THRESHOLD)
    client = TestClient(app)
    created = _request_refund(client)
    task_id = created["task_id"]
    assert created["result"]["approval_path"] == "AUTO"

    messages = _eligibility_receipt_messages(app, task_id)
    assert messages, "自动路径也应调用 check_refund_eligibility 并留下只读回执"
    summary = str(created["result"]["summary"])
    # 自动回执措辞在场（否则"不同时出现"就失去意义）。
    assert AUTO_RECEIPT_PHRASE in summary

    blob = "\n".join([*messages, summary])
    assert not ("需要人工审批" in blob and AUTO_RECEIPT_PHRASE in blob), (
        "同一任务时间线既断言要人批、又宣称自动处理：回执在撒谎（口径 §4.3/§3.1）"
    )
    # 回执仍是资格事实的中性表述，不是空串或被删空的句子。
    for message in messages:
        assert "最多可退" in message
    # 客户可见文本禁令（§4.3）：措辞里不得出现档位内部口径与阈值数值。
    for token in ("tier_id", "T1", "approver", "required_approvals", "roles", str(AUTO_THRESHOLD)):
        assert token not in blob


# --------------------------------------------------------------------------
# §8.1 / §4.2.1 不变量 5：默认阈值 0（恒人批）下这条回执与本轮之前**逐字**相等。
# 用整串相等钉死，不接受「包含」级断言。
# --------------------------------------------------------------------------


def test_default_threshold_eligibility_receipt_is_unchanged_verbatim(
    client: TestClient, app: FastAPI
) -> None:
    created = _request_refund(client)
    assert created["status"] == "WAITING_APPROVAL"

    messages = _eligibility_receipt_messages(app, created["task_id"])
    assert len(messages) == 1
    assert messages[0] == DEFAULT_ELIGIBILITY_RECEIPT


# --------------------------------------------------------------------------
# §4.2 第 6 条（2026-10-27 用户裁决）：AUTO 对账回落读执行台账载荷。
# 审批路径读审批载荷；自动执行路径没有审批行，其台账 request_payload 与审批
# 载荷由同一次 refund_payload 产生（app/services/runtime.py:725-739），是同一份
# 被摘要钉死的快照：对账不重新推断，也不得读成「未提供」。
# --------------------------------------------------------------------------


def test_auto_reconcile_reads_reason_from_ledger_payload(
    app_factory: Callable[..., FastAPI]
) -> None:
    # 口径锚点：`app/core/approval_tiers.yaml` 第 6 条（绑定校验等价：
    # AUTO 路径没有审批行，台账 request_payload 与审批载荷由同一次
    # refund_payload 产生，是同一份被摘要钉死的快照；对账不重新推断）。
    app = app_factory(
        mock_refund_mode="timeout_after", refund_approval_threshold_cents=AUTO_THRESHOLD
    )
    client = TestClient(app)
    created = _request_refund(client)
    task_id = created["task_id"]

    # 先证这确实是自动执行路径：无审批行、标记 AUTO、渠道提交后超时挂起待对账。
    assert created["result"]["approval_path"] == "AUTO"
    assert _approval_rows(app, task_id) == []
    assert created["result"]["outcome"] == "PENDING_CONFIRMATION"

    # 台账载荷就是被摘要钉死的那一份快照：断言只对照它，不硬编码枚举成员。
    payload = dict(_ledger_row(app, task_id).request_payload or {})

    # 对账走 AUTO 无审批行的分支：直接用服务层入口取回对账结果（口径 §4.2 第 6 条）。
    outcome = _reconcile_task(app, task_id)
    assert outcome.status is VerificationStatus.VERIFIED_SUCCESS

    # 核心断言：对账重建的原因与类型不为空，且逐字等于台账载荷里的值。
    # 现状缺陷：AUTO 没有审批行，reconcile_task 读空审批载荷 → 这里是 None。
    assert outcome.refund_reason is RefundReason(str(payload["reason"]))
    assert outcome.refund_type is RefundType(str(payload["refund_type"]))
    # 显示面：对账面不得把明明采集过原因的自动执行退款显示成「未提供」。
    assert refund_reason_label(outcome.refund_reason) != "未提供"
    assert refund_type_label(outcome.refund_type) != "未提供"

    # 回归保持：对账正常收尾（台账 RECONCILED），落库退款行不受影响
    # （AUTO 的退款行在执行时已按同一份台账载荷写入了原因与类型）。
    assert _ledger_row(app, task_id).status is ToolExecutionStatus.RECONCILED
    rows = _refund_rows(app)
    assert len(rows) == 1
    assert rows[0].reason is RefundReason(str(payload["reason"]))
    assert rows[0].refund_type is RefundType(str(payload["refund_type"]))
