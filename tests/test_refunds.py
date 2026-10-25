from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.errors import ForbiddenError, NotFoundError
from app.core.security import Identity
from app.core.time import utcnow
from app.models.approval import Approval
from app.models.commerce import Order, Refund
from app.models.enums import ApprovalStatus
from app.repositories.commerce import CommerceRepository
from app.services.approvals import ApprovalService
from tests.conftest import ALICE, BOB, CAROL, REVIEWER


def _request_refund(
    client: TestClient,
    headers: dict[str, str] = ALICE,
    text: str = "我要申请退款，订单 ORD-DEMO-1001，金额 129 元",
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
