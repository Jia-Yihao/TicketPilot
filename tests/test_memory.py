from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models.enums import NotificationStatus
from app.models.memory import CustomerProfile, Notification
from app.models.outbox import OutboxMessage
from tests.conftest import ALICE, BOB, CAROL

SUPPORT = {"X-Tenant-Id": "demo", "X-User-Id": "agent-1", "X-Roles": "support"}
SUPPORT_ACME = {"X-Tenant-Id": "acme", "X-User-Id": "agent-2", "X-Roles": "support"}


def _emit(
    client: TestClient,
    headers: dict[str, str],
    *,
    user_id: str = "alice",
    event_type: str = "REFUND_SETTLED",
    event_key: str = "ORD-DEMO-1001",
    context: dict[str, Any] | None = None,
) -> Any:
    return client.post(
        "/api/v1/memory/events",
        headers=headers,
        json={
            "user_id": user_id,
            "event_type": event_type,
            "event_key": event_key,
            "context": context
            if context is not None
            else {"order_id": "ORD-DEMO-1001", "amount": "129.00"},
        },
    )


def _opt_in(client: TestClient, **changes: Any) -> Any:
    payload: dict[str, Any] = {"proactive_opt_in": True, **changes}
    return client.put("/api/v1/memory", headers=ALICE, json=payload)


def _outbox_rows(app: FastAPI) -> list[OutboxMessage]:
    with app.state.session_factory() as session:
        return list(session.scalars(select(OutboxMessage)).all())


def test_profile_defaults_before_any_write(client: TestClient) -> None:
    response = client.get("/api/v1/memory", headers=ALICE)
    assert response.status_code == 200
    body = response.json()
    assert body["preferred_name"] is None
    assert body["locale"] == "zh-CN"
    assert body["preferences"] == {}
    assert body["proactive_opt_in"] is False
    assert body["muted_event_types"] == []
    assert body["max_notifications_per_day"] == 3


def test_update_round_trip_and_partial_merge(client: TestClient) -> None:
    response = client.put(
        "/api/v1/memory",
        headers=ALICE,
        json={
            "preferred_name": "Alice",
            "locale": "en-US",
            "preferences": {"tone": "concise", "product_interests": ["耳机"]},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["preferred_name"] == "Alice"
    assert body["locale"] == "en-US"
    assert body["preferences"] == {"tone": "concise", "product_interests": ["耳机"]}

    second = client.put(
        "/api/v1/memory",
        headers=ALICE,
        json={"preferences": {"contact_window": "工作日 9:00-18:00"}},
    )
    assert second.status_code == 200
    assert second.json()["preferences"] == {
        "tone": "concise",
        "product_interests": ["耳机"],
        "contact_window": "工作日 9:00-18:00",
    }
    assert second.json()["preferred_name"] == "Alice"


def test_preferred_name_rejects_contact_data(client: TestClient) -> None:
    response = client.put(
        "/api/v1/memory", headers=ALICE, json={"preferred_name": "13800138000"}
    )
    assert response.status_code == 422
    assert "personal contact" in response.json()["error"]["message"]


def test_unknown_preference_key_is_rejected(client: TestClient) -> None:
    response = client.put(
        "/api/v1/memory",
        headers=ALICE,
        json={"preferences": {"shipping_address": "某路 1 号"}},
    )
    assert response.status_code == 422
    assert "Unsupported preference" in response.json()["error"]["message"]


def test_history_summary_is_masked_not_stored_raw(app: FastAPI, client: TestClient) -> None:
    response = client.put(
        "/api/v1/memory",
        headers=ALICE,
        json={"history_summary": "上次留的手机 13800138000，地址不需要记录"},
    )
    assert response.status_code == 200
    assert "13800138000" not in response.json()["history_summary"]
    with app.state.session_factory() as session:
        profile = session.scalars(select(CustomerProfile)).one()
        assert "13800138000" not in profile.history_summary


def test_emit_requires_consent(client: TestClient) -> None:
    response = _emit(client, SUPPORT)
    assert response.status_code == 200
    assert response.json() == {
        "status": "SUPPRESSED",
        "reason": "NO_CONSENT",
        "notification": None,
    }


def test_emit_requires_support_role(client: TestClient) -> None:
    assert _emit(client, ALICE).status_code == 403
    assert client.get("/api/v1/memory/rules", headers=ALICE).status_code == 403


def test_pending_notification_waits_for_confirmation(
    app: FastAPI, client: TestClient
) -> None:
    _opt_in(client)
    response = _emit(client, SUPPORT)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PENDING_CONFIRMATION"
    assert body["notification"]["status"] == "PENDING_CONFIRMATION"
    assert body["notification"]["confirmed_at"] is None
    assert _outbox_rows(app) == []

    notification_id = body["notification"]["notification_id"]
    confirmed = client.post(
        f"/api/v1/memory/notifications/{notification_id}/confirm", headers=ALICE
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"
    assert confirmed.json()["confirmed_at"] is not None

    rows = _outbox_rows(app)
    assert len(rows) == 1
    assert rows[0].event_type == "proactive.confirmed"
    assert rows[0].dedupe_key == f"proactive:{notification_id}"
    assert rows[0].aggregate_id == notification_id


def test_confirm_is_idempotent(app: FastAPI, client: TestClient) -> None:
    _opt_in(client)
    notification_id = _emit(client, SUPPORT).json()["notification"]["notification_id"]
    first = client.post(
        f"/api/v1/memory/notifications/{notification_id}/confirm", headers=ALICE
    )
    second = client.post(
        f"/api/v1/memory/notifications/{notification_id}/confirm", headers=ALICE
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["confirmed_at"] == second.json()["confirmed_at"]
    assert len(_outbox_rows(app)) == 1


def test_cancelled_notification_cannot_be_confirmed(client: TestClient) -> None:
    _opt_in(client)
    notification_id = _emit(client, SUPPORT).json()["notification"]["notification_id"]
    cancelled = client.post(
        f"/api/v1/memory/notifications/{notification_id}/cancel", headers=ALICE
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    response = client.post(
        f"/api/v1/memory/notifications/{notification_id}/confirm", headers=ALICE
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NOTIFICATION_CANCELLED"


def test_muted_event_type_is_suppressed(client: TestClient) -> None:
    _opt_in(client, muted_event_types=["REFUND_SETTLED"])
    response = _emit(client, SUPPORT)
    assert response.json()["status"] == "SUPPRESSED"
    assert response.json()["reason"] == "UNSUBSCRIBED"


def test_duplicate_event_key_is_suppressed_once(client: TestClient) -> None:
    _opt_in(client)
    first = _emit(client, SUPPORT)
    assert first.json()["status"] == "PENDING_CONFIRMATION"
    second = _emit(client, SUPPORT)
    assert second.json()["status"] == "SUPPRESSED"
    assert second.json()["reason"] == "DUPLICATE"


def test_daily_frequency_cap(client: TestClient) -> None:
    _opt_in(client, max_notifications_per_day=1)
    first = _emit(client, SUPPORT)
    assert first.json()["status"] == "PENDING_CONFIRMATION"

    second = _emit(
        client,
        SUPPORT,
        event_type="TICKET_UPDATED",
        event_key="TICKET-1",
        context={"ticket_no": "T-1", "summary": "已处理"},
    )
    assert second.json()["status"] == "SUPPRESSED"
    assert second.json()["reason"] == "FREQUENCY_CAP"


def test_cooldown_blocks_repeat_after_cancel(client: TestClient) -> None:
    _opt_in(client)
    notification_id = _emit(client, SUPPORT).json()["notification"]["notification_id"]
    client.post(
        f"/api/v1/memory/notifications/{notification_id}/cancel", headers=ALICE
    )
    # A different event key is not a duplicate, but the rule still cools down.
    response = _emit(client, SUPPORT, event_key="ORD-DEMO-1002")
    assert response.json()["status"] == "SUPPRESSED"
    assert response.json()["reason"] == "COOLDOWN"


def test_template_context_must_be_complete(client: TestClient) -> None:
    _opt_in(client)
    response = _emit(client, SUPPORT, context={"order_id": "ORD-DEMO-1001"})
    assert response.json()["status"] == "SUPPRESSED"
    assert response.json()["reason"] == "TEMPLATE_CONTEXT_MISSING"


def test_other_user_cannot_touch_notification(client: TestClient) -> None:
    _opt_in(client)
    notification_id = _emit(client, SUPPORT).json()["notification"]["notification_id"]
    assert (
        client.post(
            f"/api/v1/memory/notifications/{notification_id}/confirm", headers=BOB
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/memory/notifications/{notification_id}/confirm", headers=CAROL
        ).status_code
        == 404
    )
    listed = client.get("/api/v1/memory/notifications", headers=BOB)
    assert listed.json()["notifications"] == []


def test_tenant_without_rules_suppresses(client: TestClient) -> None:
    assert (
        client.put(
            "/api/v1/memory", headers=CAROL, json={"proactive_opt_in": True}
        ).status_code
        == 200
    )
    response = _emit(client, SUPPORT_ACME, user_id="carol")
    assert response.json()["status"] == "SUPPRESSED"
    assert response.json()["reason"] == "NO_RULE"


def test_rules_are_listed_for_support(client: TestClient) -> None:
    response = client.get("/api/v1/memory/rules", headers=SUPPORT)
    assert response.status_code == 200
    rules = response.json()["rules"]
    assert {rule["event_type"] for rule in rules} == {
        "REFUND_SETTLED",
        "TICKET_UPDATED",
        "DELIVERY_EXCEPTION",
    }
    assert all(rule["requires_confirmation"] for rule in rules)


def test_export_reports_counts_without_values(client: TestClient) -> None:
    _opt_in(client)
    _emit(client, SUPPORT)
    response = client.get("/api/v1/memory/export", headers=ALICE)
    assert response.status_code == 200
    body = response.json()
    assert body["redacted"] is True
    assert body["notification_counts"] == {"PENDING_CONFIRMATION": 1}
    assert body["profile"]["proactive_opt_in"] is True


def test_delete_erases_profile_notifications_and_queue(
    app: FastAPI, client: TestClient
) -> None:
    _opt_in(client)
    notification_id = _emit(client, SUPPORT).json()["notification"]["notification_id"]
    client.post(
        f"/api/v1/memory/notifications/{notification_id}/confirm", headers=ALICE
    )

    response = client.delete("/api/v1/memory", headers=ALICE)
    assert response.status_code == 204

    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(CustomerProfile.id))) == 0
        assert session.scalar(select(func.count(Notification.id))) == 0
        statuses = session.scalars(select(OutboxMessage.status)).all()
        assert statuses == ["CANCELLED"]

    assert client.get("/api/v1/memory", headers=ALICE).json()["proactive_opt_in"] is False


def test_returned_notification_status_is_persisted(app: FastAPI, client: TestClient) -> None:
    _opt_in(client)
    notification_id = _emit(client, SUPPORT).json()["notification"]["notification_id"]
    client.post(
        f"/api/v1/memory/notifications/{notification_id}/confirm", headers=ALICE
    )
    with app.state.session_factory() as session:
        row = session.get(Notification, notification_id)
        assert row is not None
        assert row.status == NotificationStatus.CONFIRMED.value
        assert row.confirmed_at is not None
