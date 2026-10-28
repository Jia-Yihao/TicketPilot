from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.errors import ForbiddenError, NotFoundError
from app.core.security import Identity
from app.core.time import utcnow
from app.models.approval import Approval
from app.models.audit import AuditEvent
from app.models.commerce import Order, Refund
from app.models.enums import ApprovalStatus, RefundReason, RefundStatus, RefundType
from app.repositories.commerce import CommerceRepository
from app.services.approvals import ApprovalService, canonical_digest
from app.tools.base import RetryPolicy, ToolContext
from app.tools.executor import FailureKind, ToolExecutionError, ToolExecutor
from tests.conftest import ALICE, BOB, CAROL, REVIEWER


def _request_refund(
    client: TestClient,
    headers: dict[str, str] = ALICE,
    text: str = "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，商品有质量问题",
) -> dict:
    response = client.post("/api/v1/tasks", headers=headers, json={"request": text})
    assert response.status_code == 201, response.text
    return response.json()


def _approve(client: TestClient, task_id: str, headers: dict[str, str] = REVIEWER) -> dict:
    response = client.post(
        f"/api/v1/tasks/{task_id}/approve",
        headers=headers,
        json={"decision": "APPROVE"},
    )
    return response


def _order(app: FastAPI, order_id: str = "ORD-DEMO-1001") -> Order:
    with app.state.session_factory() as session:
        return session.get(Order, order_id)


def test_refund_waits_for_approval_then_verifies_success(
    client: TestClient, app: FastAPI
) -> None:
    created = _request_refund(client)
    assert created["status"] == "WAITING_APPROVAL"
    assert created["result"]["outcome"] == "WAITING_APPROVAL"
    assert created["pending_approval"]["amount_cents"] == 12900
    assert created["tool_execution"]["status"] == "PENDING"

    approved = _approve(client, created["task_id"])
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "SUCCEEDED"
    assert body["result"]["outcome"] == "VERIFIED_SUCCESS"
    assert body["latest_approval"]["status"] == "CONSUMED"
    assert body["tool_execution"]["status"] == "RECONCILED"
    assert _order(app).refundable_cents == 0

    duplicate = _approve(client, created["task_id"])
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "CONFLICT"


def test_self_approval_is_blocked(client: TestClient) -> None:
    created = _request_refund(client)
    response = _approve(
        client,
        created["task_id"],
        headers={**ALICE, "X-Roles": "approver"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    current = client.get(f"/api/v1/tasks/{created['task_id']}", headers=ALICE).json()
    assert current["status"] == "WAITING_APPROVAL"


def test_admin_cannot_self_approve(client: TestClient) -> None:
    created = _request_refund(client)
    response = _approve(
        client,
        created["task_id"],
        headers={**ALICE, "X-Roles": "approver,admin"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    current = client.get(f"/api/v1/tasks/{created['task_id']}", headers=ALICE).json()
    assert current["status"] == "WAITING_APPROVAL"


def test_cross_tenant_approver_is_rejected(client: TestClient) -> None:
    created = _request_refund(client)
    response = _approve(
        client,
        created["task_id"],
        headers={**CAROL, "X-Roles": "approver"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_refund_of_another_customers_order_never_reaches_approval(
    client: TestClient, app: FastAPI
) -> None:
    """The identity scope is what makes a wrong ``order_id`` harmless.

    Slot extraction (rules today, a model tomorrow) can hand the workflow any
    order id it likes; ``require_order`` resolves it inside the caller's tenant
    and user, so a foreign id ends the task before an approval exists and
    before a tool execution row is written.
    """
    created = _request_refund(client, headers=BOB)

    assert created["status"] == "FAILED"
    assert created["result"]["outcome"] == "FAILED"
    assert created["result"]["error_code"] == "NOT_FOUND"
    assert created["result"]["summary"] == "Order not found."
    assert created["pending_approval"] is None
    assert created["tool_execution"] is None
    assert _order(app, "ORD-DEMO-1001").refundable_cents == 12900


def test_approval_service_rejects_cross_tenant_reviewer(
    client: TestClient, app: FastAPI
) -> None:
    created = _request_refund(client)
    with app.state.session_factory() as session:
        service = ApprovalService(session)
        with pytest.raises(NotFoundError):
            service.decide(
                task_id=created["task_id"],
                approval_id=created["pending_approval"]["approval_id"],
                decision="APPROVE",
                reviewer=Identity("acme", "carol", frozenset({"approver"})),
            )


def test_approval_service_requires_approver_role(
    client: TestClient, app: FastAPI
) -> None:
    created = _request_refund(client)
    with app.state.session_factory() as session:
        service = ApprovalService(session)
        with pytest.raises(ForbiddenError):
            service.decide(
                task_id=created["task_id"],
                approval_id=created["pending_approval"]["approval_id"],
                decision="APPROVE",
                reviewer=Identity("demo", "reviewer", frozenset()),
            )


def test_second_refund_task_is_blocked_while_approval_is_open(
    client: TestClient, app: FastAPI
) -> None:
    first = _request_refund(client)
    assert first["status"] == "WAITING_APPROVAL"

    second = _request_refund(client)
    assert second["status"] == "FAILED"
    assert second["result"]["outcome"] == "FAILED"
    assert "open approval" in second["result"]["summary"]
    with app.state.session_factory() as session:
        approvals = list(session.scalars(select(Approval)))
    assert len(approvals) == 1
    assert approvals[0].status is ApprovalStatus.PENDING
    assert client.get(f"/api/v1/tasks/{first['task_id']}", headers=ALICE).json()[
        "status"
    ] == "WAITING_APPROVAL"


def test_approver_role_is_required(client: TestClient) -> None:
    created = _request_refund(client)
    response = _approve(client, created["task_id"], headers=BOB)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_rejection_cancels_execution(client: TestClient, app: FastAPI) -> None:
    created = _request_refund(client)
    response = client.post(
        f"/api/v1/tasks/{created['task_id']}/approve",
        headers=REVIEWER,
        json={"decision": "REJECT", "reason": "金额需要复核"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "REJECTED"
    assert body["latest_approval"]["status"] == "REJECTED"
    assert body["tool_execution"]["status"] == "CANCELLED"
    with app.state.session_factory() as session:
        refunds = list(session.scalars(select(Refund)))
    assert refunds == []


def test_expired_approval_is_not_executed(client: TestClient, app: FastAPI) -> None:
    created = _request_refund(client)
    approval_id = created["pending_approval"]["approval_id"]
    with app.state.session_factory() as session:
        approval = session.get(Approval, approval_id)
        approval.expires_at = utcnow() - timedelta(minutes=1)
        session.commit()

    response = _approve(client, created["task_id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "APPROVAL_EXPIRED"

    current = client.get(f"/api/v1/tasks/{created['task_id']}", headers=ALICE).json()
    assert current["status"] == "EXPIRED"
    assert current["latest_approval"]["status"] == "EXPIRED"
    assert current["tool_execution"]["status"] == "CANCELLED"


def test_resource_version_change_invalidates_approval(
    client: TestClient, app: FastAPI
) -> None:
    created = _request_refund(client)
    with app.state.session_factory() as session:
        order = session.get(Order, "ORD-DEMO-1001")
        order.version += 1
        session.commit()

    response = _approve(client, created["task_id"])
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["result"]["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert body["result"]["reason_code"] == "RESOURCE_VERSION_CHANGED"
    assert body["latest_approval"]["status"] == "INVALID"
    assert body["tool_execution"]["status"] == "CANCELLED"
    with app.state.session_factory() as session:
        assert list(session.scalars(select(Refund))) == []


def test_second_refund_is_rejected_by_server_side_rules(
    client: TestClient, app: FastAPI
) -> None:
    created = _request_refund(client)
    assert _approve(client, created["task_id"]).status_code == 200

    second = _request_refund(client)
    assert second["status"] == "SUCCEEDED"
    assert second["result"]["outcome"] == "FAILED"
    assert "ALREADY_REFUNDED" in second["result"]["summary"]
    with app.state.session_factory() as session:
        assert len(list(session.scalars(select(Refund)))) == 1


def test_timeout_after_commit_reconciles_without_double_refund(app_factory) -> None:
    app = app_factory(mock_refund_mode="timeout_after")
    client = TestClient(app)
    created = _request_refund(client)

    approved = _approve(client, created["task_id"])
    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "SUCCEEDED"
    assert body["result"]["outcome"] == "PENDING_CONFIRMATION"
    assert body["tool_execution"]["status"] == "UNKNOWN"
    assert _order(app).refundable_cents == 12900

    assert body["latest_approval"]["status"] == "APPROVED"

    first = app.state.runtime.reconcile(created["task_id"])
    assert first["result"]["outcome"] == "VERIFIED_SUCCESS"
    assert first["latest_approval"]["status"] == "CONSUMED"
    assert first["tool_execution"]["status"] == "RECONCILED"
    assert _order(app).refundable_cents == 0

    second = app.state.runtime.reconcile(created["task_id"])
    assert second["result"]["outcome"] == "VERIFIED_SUCCESS"
    assert _order(app).refundable_cents == 0
    with app.state.session_factory() as session:
        assert len(list(session.scalars(select(Refund)))) == 1


def test_timeout_before_commit_stays_pending(app_factory) -> None:
    app = app_factory(mock_refund_mode="timeout_before")
    client = TestClient(app)
    created = _request_refund(client)

    response = _approve(client, created["task_id"])
    assert response.status_code == 200
    body = response.json()
    assert body["result"]["outcome"] == "PENDING_CONFIRMATION"
    reconciled = app.state.runtime.reconcile(created["task_id"])
    assert reconciled["result"]["outcome"] == "PENDING_CONFIRMATION"
    assert reconciled["result"]["reason_code"] == "NOT_FOUND_YET"
    assert _order(app).refundable_cents == 12900


def test_cancel_waiting_approval(client: TestClient, app: FastAPI) -> None:
    created = _request_refund(client)
    response = client.post(f"/api/v1/tasks/{created['task_id']}/cancel", headers=ALICE)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CANCELLED"
    assert body["pending_approval"] is None
    assert body["latest_approval"]["status"] == "CANCELLED"
    assert body["tool_execution"]["status"] == "CANCELLED"
    assert _order(app).refundable_cents == 12900


def test_cannot_cancel_terminal_task(client: TestClient) -> None:
    created = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={"request": "帮我查询订单 ORD-DEMO-1001"},
    ).json()
    assert created["status"] == "SUCCEEDED"

    response = client.post(f"/api/v1/tasks/{created['task_id']}/cancel", headers=ALICE)
    assert response.status_code == 409


def test_cross_tenant_task_is_hidden(client: TestClient) -> None:
    created = _request_refund(client)
    response = client.get(f"/api/v1/tasks/{created['task_id']}", headers=CAROL)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_provider_failure_consumes_approval_and_allows_retry(app_factory) -> None:
    app = app_factory(mock_refund_mode="fail")
    client = TestClient(app)
    created = _request_refund(client)

    body = _approve(client, created["task_id"]).json()
    assert body["status"] == "FAILED"
    assert body["result"]["outcome"] == "FAILED"
    assert body["latest_approval"]["status"] == "CONSUMED"
    assert body["tool_execution"]["status"] == "FAILED"
    assert _order(app).refundable_cents == 12900

    retry = _request_refund(client)
    assert retry["status"] == "WAITING_APPROVAL"


def test_refund_balance_update_is_conditional(app: FastAPI) -> None:
    with app.state.session_factory() as session:
        repo = CommerceRepository(session)
        assert repo.apply_refund_balance("ORD-DEMO-1001", 12900) is True
        assert repo.apply_refund_balance("ORD-DEMO-1001", 12900) is False
        session.commit()

    with app.state.session_factory() as session:
        order = session.get(Order, "ORD-DEMO-1001")
        assert order.refundable_cents == 0
        assert order.version == 2


def test_open_approval_unique_index_exists(app: FastAPI) -> None:
    with app.state.engine.connect() as connection:
        definition = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'index' AND name = 'uq_approvals_open_per_resource'"
            )
        ).scalar()

    assert definition is not None
    assert "UNIQUE" in definition.upper()
    assert "PENDING" in definition
    assert "APPROVED" in definition


def test_the_approval_snapshot_carries_the_reason_and_the_type(
    client: TestClient, app: FastAPI
) -> None:
    """退款快照带上原因与类型：审批人批准的就是摘要钉死的这件事。"""

    created = _request_refund(client)

    with app.state.session_factory() as session:
        approval = session.get(Approval, created["pending_approval"]["approval_id"])
    assert approval is not None
    assert approval.action_payload["reason"] == "QUALITY"
    assert approval.action_payload["refund_type"] == "FULL"
    assert approval.action_payload["amount_cents"] == 12900


# --- 任务 7：退款落库三列与展示 --------------------------------------------


def _refund_rows(app: FastAPI) -> list[Refund]:
    with app.state.session_factory() as session:
        return list(session.scalars(select(Refund)))


def _approval_detail(client: TestClient, approval_id: str) -> dict:
    response = client.get(f"/api/v1/approvals/{approval_id}", headers=REVIEWER)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_refund_record_carries_the_reason_and_the_type(
    client: TestClient, app: FastAPI
) -> None:
    """落库：原因与类型写进退款单，写进去的就是提交的那两个值。

    口径 §5.2（三列均可空、新记录应用层必填）、§2（新记录约束）。
    """

    created = _request_refund(client)
    assert _approve(client, created["task_id"]).status_code == 200

    rows = _refund_rows(app)
    assert len(rows) == 1
    assert rows[0].reason is RefundReason.QUALITY
    assert rows[0].refund_type is RefundType.FULL
    # 没有提交补充说明，就不该凭空长出一段说明。
    assert rows[0].reason_note is None


def test_the_note_lands_only_for_the_other_reason(
    client: TestClient, app: FastAPI
) -> None:
    """补充说明只在 ``OTHER`` 时落库，且不进载荷、因此不进摘要。

    口径 §2（``reason_note`` 仅 ``reason=OTHER`` 时可写）、§4.2（不进摘要）。
    """

    response = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={
            "request": "我要申请退款，订单 ORD-DEMO-1001，金额 129 元，说不清",
            "context": {"refund_reason": "OTHER", "reason_note": "坐席说可以退"},
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    approval_id = created["pending_approval"]["approval_id"]
    assert approval_id

    with app.state.session_factory() as session:
        approval = session.get(Approval, approval_id)
    assert approval is not None
    assert "reason_note" not in (approval.action_payload or {})
    assert canonical_digest(dict(approval.action_payload or {})) == approval.action_digest

    assert _approve(client, created["task_id"]).status_code == 200

    rows = _refund_rows(app)
    assert len(rows) == 1
    assert rows[0].reason is RefundReason.OTHER
    assert rows[0].reason_note == "坐席说可以退"


def test_a_provider_failure_row_still_carries_the_reason_and_the_type(
    app_factory,
) -> None:
    """失败路径同样落库：渠道拒绝的那张单子也得说清是哪一次退款。"""

    app = app_factory(mock_refund_mode="fail")
    client = TestClient(app)
    created = _request_refund(client)

    assert _approve(client, created["task_id"]).status_code == 200

    rows = _refund_rows(app)
    assert len(rows) == 1
    assert rows[0].status is RefundStatus.FAILED
    assert rows[0].reason is RefundReason.QUALITY
    assert rows[0].refund_type is RefundType.FULL


def test_a_legacy_approval_is_not_mapped_to_other_or_full(
    client: TestClient, app: FastAPI
) -> None:
    """历史载荷没有这两个键：读出是「未提供」，落库是 NULL，不是 OTHER / FULL。

    口径 §2（历史退款读出显示「未提供」，不映射成 OTHER / FULL）。
    """

    created = _request_refund(client)
    approval_id = created["pending_approval"]["approval_id"]
    with app.state.session_factory() as session:
        approval = session.get(Approval, approval_id)
        assert approval is not None
        legacy_payload = {
            key: value
            for key, value in dict(approval.action_payload or {}).items()
            if key not in {"reason", "refund_type"}
        }
        approval.action_payload = legacy_payload
        approval.action_digest = canonical_digest(legacy_payload)
        session.commit()

    detail = _approval_detail(client, approval_id)
    assert detail["refund_reason"] is None
    assert detail["refund_reason_label"] == "未提供"
    assert detail["refund_type"] is None
    assert detail["refund_type_label"] == "未提供"

    assert _approve(client, created["task_id"]).status_code == 200

    rows = _refund_rows(app)
    assert len(rows) == 1
    assert rows[0].reason is None
    assert rows[0].refund_type is None


def test_the_approval_detail_carries_the_chinese_names(client: TestClient) -> None:
    """审批详情给出中文唯一名；顶层的 ``reason`` 仍然是审批决定原因。

    口径 §1.0（裸名 ``reason`` 不承载退款原因）、§5.5（审批详情展示中文唯一名）。
    """

    created = _request_refund(client)
    approval_id = created["pending_approval"]["approval_id"]

    detail = _approval_detail(client, approval_id)
    assert detail["refund_reason"] == "QUALITY"
    assert detail["refund_reason_label"] == "质量问题"
    assert detail["refund_type"] == "FULL"
    assert detail["refund_type_label"] == "全额退款"
    # 待审批时没有审批决定原因；退款原因不占这个位置。
    assert detail["reason"] is None

    rejected = client.post(
        f"/api/v1/tasks/{created['task_id']}/approve",
        headers=REVIEWER,
        json={"decision": "REJECT", "reason": "金额需要复核"},
    )
    assert rejected.status_code == 200

    detail = _approval_detail(client, approval_id)
    assert detail["reason"] == "金额需要复核"
    assert detail["refund_reason"] == "QUALITY"
    assert detail["refund_reason_label"] == "质量问题"


def test_the_public_receipt_carries_the_names_and_never_the_note(
    client: TestClient, app: FastAPI
) -> None:
    """公开回执带原因与类型的中文唯一名，且不带补充说明。

    口径 §5.5（回执须展示中文唯一名）、§4.2（``reason_note`` 不进公开回执）。
    """

    created = _request_refund(client)
    approval_id = created["pending_approval"]["approval_id"]
    with app.state.session_factory() as session:
        approval = session.get(Approval, approval_id)
        assert approval is not None
        approval.status = ApprovalStatus.APPROVED
        approval.reviewer_id = "reviewer"
        session.commit()

        services = app.state.runtime._services(session)
        manifest = services.gateway.registry.require("request_refund").manifest
        assert "refund_reason_label" in manifest.result_schema["properties"]
        assert "refund_type_label" in manifest.result_schema["properties"]
        assert "reason_note" not in manifest.result_schema["properties"]

        result = services.gateway.call(
            "request_refund",
            ToolContext(
                identity=Identity("demo", "alice", frozenset()),
                task_id=created["task_id"],
                approval_id=approval_id,
                idempotency_key=f"refund:{created['task_id']}",
            ),
            {},
        )

    assert result.data["refund_reason_label"] == "质量问题"
    assert result.data["refund_type_label"] == "全额退款"
    assert "reason_note" not in result.data
    assert "质量问题" in result.message
    assert "全额退款" in result.message


def test_an_unconfirmed_refund_is_a_failure_not_a_receipt(app_factory) -> None:
    """R3：结果未知落到失败分支，审计不记成功，放开重试也不产生二次退款。

    ``timeout_before`` 剧本下台账停在 ``UNKNOWN``、业务结论是
    ``PENDING_CONFIRMATION``：退款可能已经发生，也可能没有，只有对账能回答。
    这既不是成功回执，也不是可重放的普通失败——工具必须以「结果未知」这个
    失败码出去，网关因此记 ERROR；即使把重试策略放开到含 unknown，执行器
    也不会再发一次。
    """
    app = app_factory(mock_refund_mode="timeout_before")
    client = TestClient(app)
    created = _request_refund(client)
    approval_id = created["pending_approval"]["approval_id"]

    approved = _approve(client, created["task_id"]).json()
    assert approved["result"]["outcome"] == "PENDING_CONFIRMATION"

    ctx = ToolContext(
        identity=Identity("demo", "alice", frozenset()),
        task_id=created["task_id"],
        approval_id=approval_id,
        idempotency_key=f"refund:{created['task_id']}",
    )
    with app.state.session_factory() as session:
        services = app.state.runtime._services(session)
        with pytest.raises(ToolExecutionError) as caught:
            services.gateway.call_outcome("request_refund", ctx, {})
        session.commit()

    assert caught.value.kind is FailureKind.UNKNOWN
    assert caught.value.code == "OUTCOME_UNKNOWN"
    assert caught.value.retryable is False
    assert caught.value.degradation is not None
    assert caught.value.degradation.kind == "stop"

    with app.state.session_factory() as session:
        audits = list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.action == "tool.request_refund")
            )
        )
        assert [row.outcome for row in audits] == ["ERROR"]
        assert audits[0].detail["failure_kind"] == "unknown"
        assert list(session.scalars(select(Refund))) == []

    # 放开重试：策略允许 unknown，执行器仍然只发一次。
    with app.state.session_factory() as session:
        services = app.state.runtime._services(session)
        tool = services.gateway.registry.require("request_refund")
        loose = replace(
            tool.manifest,
            retry=RetryPolicy(
                max_attempts=3, backoff_ms=0, retry_on=("timeout", "unknown")
            ),
        )
        outcome = ToolExecutor(sleeper=lambda _seconds: None).execute(
            loose, tool.handler, ctx, {}
        )

    assert outcome.failure is FailureKind.UNKNOWN
    assert outcome.attempts == 1
    with app.state.session_factory() as session:
        assert list(session.scalars(select(Refund))) == []
