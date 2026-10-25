"""The outbound half of the channel story: a confirmed notice that leaves.

The renderer, the transport and the queue are tested together here, because the
interesting failures live between them — a message that reports itself sent
without a transport, a target taken from the payload, a consent withdrawn after
confirmation.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.channels.outbound import (
    LocalFileTransport,
    OutboundDeliveryError,
    OutboundNotice,
    ProactiveSender,
    RefusingTransport,
    build_transport,
    resolve_address,
)
from app.channels.render import render_notice
from app.core.config import Settings
from app.core.security import Identity
from app.core.time import utcnow
from app.models.audit import AuditEvent
from app.models.enums import Channel, NotificationStatus, OutboxStatus
from app.models.memory import Notification
from app.models.outbox import OutboxMessage
from app.services.outbox import OutboxService
from tests.conftest import ALICE, BOB

SUPPORT = {"X-Tenant-Id": "demo", "X-User-Id": "agent-1", "X-Roles": "support"}

_LONG_SENTENCE = "订单 ORD-DEMO-1001 的退款 129.00 元已到账，请注意查收，此处补足长度。"
LONG_BODY = _LONG_SENTENCE * 6


class SpyTransport:
    """Records what the queue handed over instead of writing a file."""

    name = "spy"

    def __init__(self) -> None:
        self.sent: list[OutboundNotice] = []

    def send(self, notice: OutboundNotice) -> None:
        self.sent.append(notice)


def _drop_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


def _notice(**overrides: Any) -> OutboundNotice:
    values: dict[str, Any] = {
        "notification_id": "ntf_test",
        "tenant_id": "demo",
        "user_id": "alice",
        "channel": Channel.EMAIL,
        "address": "alice@example.com",
        "subject": "TicketPilot 提醒 · 退款到账提醒",
        "text": "您好：\n退款已到账。",
        "truncated": False,
        "event_type": "REFUND_SETTLED",
    }
    values.update(overrides)
    return OutboundNotice(**values)


def _mail_app(
    app_factory: Callable[..., FastAPI],
    tmp_path: Path,
    *,
    transport: str = "local_file",
) -> tuple[FastAPI, TestClient, Path]:
    drop = tmp_path / "outbound" / "drop.jsonl"
    application = app_factory(
        channel_email_enabled=True,
        channel_email_secret="mail-secret",
        channel_email_identity_map={
            "alice@example.com": "demo:alice",
            "bob@example.com": "demo:bob",
        },
        channel_outbound_transport=transport,
        channel_outbound_drop_file=str(drop),
    )
    return application, TestClient(application), drop


def _opt_in(client: TestClient, headers: dict[str, str]) -> None:
    response = client.put(
        "/api/v1/memory",
        headers=headers,
        json={
            "proactive_opt_in": True,
            "preferences": {"contact_channel": "EMAIL"},
        },
    )
    assert response.status_code == 200, response.text


def _emit(client: TestClient, user_id: str, event_key: str) -> str:
    response = client.post(
        "/api/v1/memory/events",
        headers=SUPPORT,
        json={
            "user_id": user_id,
            "event_type": "REFUND_SETTLED",
            "event_key": event_key,
            "context": {"order_id": event_key, "amount": "129.00"},
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["notification"]["notification_id"])


def _queue_notice(client: TestClient) -> str:
    """Opt in, emit one event and confirm it, leaving a queued message."""
    _opt_in(client, ALICE)
    notification_id = _emit(client, "alice", "ORD-DEMO-1001")
    confirmed = client.post(
        f"/api/v1/memory/notifications/{notification_id}/confirm", headers=ALICE
    )
    assert confirmed.status_code == 200, confirmed.text
    return notification_id


def test_email_notice_carries_subject_and_signature() -> None:
    rendered = render_notice(
        Channel.EMAIL, title="退款到账提醒", body="订单 ORD-DEMO-1001 退款已到账。"
    )
    assert rendered.subject == "TicketPilot 提醒 · 退款到账提醒"
    assert rendered.text.startswith("您好：")
    assert "—— TicketPilot 客服团队" in rendered.text
    assert "处理时限：24 小时内首次回复" in rendered.text
    assert rendered.truncated is False


def test_im_notice_is_cut_to_the_channel_limit() -> None:
    rendered = render_notice(Channel.WECHAT, title="退款到账提醒", body=LONG_BODY)
    assert len(rendered.text) <= 200
    assert rendered.truncated is True
    assert rendered.text.endswith("…")


def test_sms_notice_keeps_the_unsubscribe_line() -> None:
    rendered = render_notice(Channel.SMS, title="退款到账提醒", body=LONG_BODY)
    assert len(rendered.text) <= 70
    assert rendered.text.endswith(" 回复TD退订")


def test_voice_notice_respects_its_limit_and_offers_handoff() -> None:
    rendered = render_notice(Channel.VOICE, title="退款到账提醒", body=LONG_BODY)
    assert len(rendered.text) <= 120
    assert "「转人工」" in rendered.text


def test_default_transport_refuses_everything(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'unused.db'}",
    )
    transport = build_transport(settings)
    assert isinstance(transport, RefusingTransport)
    try:
        transport.send(_notice())
    except OutboundDeliveryError as exc:
        assert exc.reason == "NO_TRANSPORT"
    else:  # pragma: no cover - the refusal is the whole point
        raise AssertionError("the default transport must refuse")


def test_drop_transport_needs_a_file(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'unused.db'}",
        channel_outbound_transport="local_file",
    )
    transport = build_transport(settings)
    try:
        transport.send(_notice())
    except OutboundDeliveryError as exc:
        assert exc.reason == "DROP_FILE_MISSING"
    else:  # pragma: no cover
        raise AssertionError("a drop transport without a path must refuse")


def test_local_drop_transport_appends_one_line_per_notice(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "drop.jsonl"
    transport = LocalFileTransport(path)
    transport.send(_notice())
    transport.send(_notice(notification_id="ntf_test_2", truncated=True))

    lines = _drop_lines(path)
    assert len(lines) == 2
    assert lines[0]["channel"] == "EMAIL"
    assert lines[0]["address"] == "alice@example.com"
    assert lines[1]["notification_id"] == "ntf_test_2"
    assert lines[1]["truncated"] is True


def test_production_refuses_the_local_drop_transport() -> None:
    try:
        Settings(
            environment="prod",
            database_url="postgresql+psycopg://app:app@localhost:5432/app",
            auto_create_tables=False,
            identity_adapter="oidc",
            refund_provider="http",
            docs_enabled=False,
            channel_outbound_transport="local_file",
            channel_outbound_drop_file="/tmp/drop.jsonl",
        )
    except ValueError as exc:
        assert "CHANNEL_OUTBOUND_TRANSPORT" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("production must not ship a demo sink")


def test_address_comes_from_config_not_the_message(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'unused.db'}",
        channel_email_identity_map={"alice@example.com": "demo:alice"},
    )
    assert (
        resolve_address(
            settings, channel=Channel.EMAIL, tenant_id="demo", user_id="alice"
        )
        == "alice@example.com"
    )

    refused = ((Channel.SMS, "NO_ADDRESS_SOURCE"), (Channel.EMAIL, "NO_ADDRESS"))
    for channel, reason in refused:
        try:
            resolve_address(
                settings, channel=channel, tenant_id="demo", user_id="nobody"
            )
        except OutboundDeliveryError as exc:
            assert exc.reason == reason
        else:  # pragma: no cover
            raise AssertionError("an unknown target must be refused, not guessed")


def test_confirmed_notice_reaches_sent_and_the_drop_file(
    app_factory: Callable[..., FastAPI], tmp_path: Path
) -> None:
    application, client, drop = _mail_app(app_factory, tmp_path)
    notification_id = _queue_notice(client)

    delivered = client.post("/api/v1/outbox/deliver", headers=SUPPORT)
    assert delivered.status_code == 200, delivered.text
    assert delivered.json() == {
        "claimed": 1,
        "sent": 1,
        "failed": 0,
        "dead_lettered": 0,
    }

    lines = _drop_lines(drop)
    assert len(lines) == 1
    record = lines[0]
    assert record["notification_id"] == notification_id
    assert record["channel"] == "EMAIL"
    assert record["address"] == "alice@example.com"
    assert str(record["subject"]).startswith("TicketPilot 提醒 · ")
    assert "ORD-DEMO-1001" in str(record["text"])
    assert "—— TicketPilot 客服团队" in str(record["text"])

    listed = client.get("/api/v1/memory/notifications", headers=ALICE)
    assert listed.json()["notifications"][0]["status"] == NotificationStatus.SENT.value

    with application.state.session_factory() as session:
        row = session.scalars(select(OutboxMessage)).one()
        assert row.status == OutboxStatus.SENT.value
        assert row.sent_at is not None
        event = session.scalars(
            select(AuditEvent).where(AuditEvent.action == "proactive.delivered")
        ).one()
        assert event.detail["channel"] == "EMAIL"
        assert event.detail["transport"] == "local_file"
        # The audit trail records that a message left, not where to reach her.
        assert "alice@example.com" not in json.dumps(event.detail, ensure_ascii=False)

    # A second pass has nothing to send: one notice, one line in the drop file.
    again = client.post("/api/v1/outbox/deliver", headers=SUPPORT)
    assert again.json()["claimed"] == 0
    assert len(_drop_lines(drop)) == 1


def test_default_transport_leaves_the_notice_confirmed_not_sent(
    app_factory: Callable[..., FastAPI], tmp_path: Path
) -> None:
    application, client, drop = _mail_app(app_factory, tmp_path, transport="none")
    notification_id = _queue_notice(client)

    report = client.post("/api/v1/outbox/deliver", headers=SUPPORT)
    assert report.json() == {
        "claimed": 1,
        "sent": 0,
        "failed": 1,
        "dead_lettered": 0,
    }
    assert _drop_lines(drop) == []

    with application.state.session_factory() as session:
        row = session.scalars(select(OutboxMessage)).one()
        assert row.status == OutboxStatus.PENDING.value
        assert row.retry_count == 1
        assert "NO_TRANSPORT" in (row.last_error or "")
        note = session.get(Notification, notification_id)
        assert note is not None
        assert note.status == NotificationStatus.CONFIRMED.value


def test_notice_without_a_channel_preference_is_not_sent(
    app_factory: Callable[..., FastAPI], tmp_path: Path
) -> None:
    application, client, drop = _mail_app(app_factory, tmp_path)
    assert (
        client.put(
            "/api/v1/memory", headers=ALICE, json={"proactive_opt_in": True}
        ).status_code
        == 200
    )
    notification_id = _emit(client, "alice", "ORD-DEMO-1001")
    assert (
        client.post(
            f"/api/v1/memory/notifications/{notification_id}/confirm", headers=ALICE
        ).status_code
        == 200
    )

    report = client.post("/api/v1/outbox/deliver", headers=SUPPORT)
    assert report.json()["sent"] == 0
    assert report.json()["failed"] == 1
    assert _drop_lines(drop) == []

    with application.state.session_factory() as session:
        row = session.scalars(select(OutboxMessage)).one()
        assert "NO_CHANNEL" in (row.last_error or "")


def test_revoked_consent_stops_a_queued_notice(
    app_factory: Callable[..., FastAPI], tmp_path: Path
) -> None:
    application, client, drop = _mail_app(app_factory, tmp_path)
    _queue_notice(client)

    opt_out = client.put(
        "/api/v1/memory", headers=ALICE, json={"proactive_opt_in": False}
    )
    assert opt_out.status_code == 200

    report = client.post("/api/v1/outbox/deliver", headers=SUPPORT)
    assert report.json()["sent"] == 0
    assert _drop_lines(drop) == []

    with application.state.session_factory() as session:
        row = session.scalars(select(OutboxMessage)).one()
        assert "NO_CONSENT" in (row.last_error or "")
        note = session.scalars(select(Notification)).one()
        assert note.status == NotificationStatus.CONFIRMED.value


def test_muted_event_type_stops_a_queued_notice(
    app_factory: Callable[..., FastAPI], tmp_path: Path
) -> None:
    """Muting after confirming is a withdrawal of permission for that message."""
    application, client, drop = _mail_app(app_factory, tmp_path)
    notification_id = _queue_notice(client)

    muted = client.put(
        "/api/v1/memory",
        headers=ALICE,
        json={"muted_event_types": ["REFUND_SETTLED"]},
    )
    assert muted.status_code == 200, muted.text

    report = client.post("/api/v1/outbox/deliver", headers=SUPPORT)
    assert report.json()["sent"] == 0
    assert _drop_lines(drop) == []

    with application.state.session_factory() as session:
        row = session.scalars(select(OutboxMessage)).one()
        assert "UNSUBSCRIBED" in (row.last_error or "")
        note = session.get(Notification, notification_id)
        assert note is not None
        assert note.status == NotificationStatus.CONFIRMED.value


def test_payload_cannot_redirect_a_notice_to_another_customer(
    app_factory: Callable[..., FastAPI], tmp_path: Path
) -> None:
    application, client, drop = _mail_app(app_factory, tmp_path)
    alice_notice = _queue_notice(client)

    _opt_in(client, BOB)
    bob_notice = _emit(client, "bob", "ORD-DEMO-1002")
    with application.state.session_factory() as session:
        bob = session.get(Notification, bob_notice)
        assert bob is not None
        # Bob's notice is in the state a redirect wants, but Bob has no queued
        # row of his own: the only message in the queue belongs to Alice.
        bob.status = NotificationStatus.CONFIRMED.value
        bob.confirmed_at = utcnow()
        session.commit()

    with application.state.session_factory() as session:
        row = session.scalars(
            select(OutboxMessage).where(OutboxMessage.aggregate_id == alice_notice)
        ).one()
        # A queued row that names someone else's notice is refused, not followed.
        row.payload = {
            **row.payload,
            "notification_id": bob_notice,
            "channel": "SMS",
            "address": "attacker@example.com",
        }
        session.commit()

    report = client.post("/api/v1/outbox/deliver", headers=SUPPORT)
    assert report.json()["sent"] == 0
    assert _drop_lines(drop) == []

    with application.state.session_factory() as session:
        row = session.scalars(
            select(OutboxMessage).where(OutboxMessage.aggregate_id == alice_notice)
        ).one()
        assert "NOT_FOUND" in (row.last_error or "")
        bob_row = session.get(Notification, bob_notice)
        assert bob_row is not None
        assert bob_row.status == NotificationStatus.CONFIRMED.value


def test_contact_channel_accepts_names_and_rejects_addresses(client: TestClient) -> None:
    accepted = client.put(
        "/api/v1/memory",
        headers=ALICE,
        json={"preferences": {"contact_channel": "wechat"}},
    )
    assert accepted.status_code == 200
    assert accepted.json()["preferences"] == {"contact_channel": "WECHAT"}

    cleared = client.put(
        "/api/v1/memory",
        headers=ALICE,
        json={"preferences": {"contact_channel": None}},
    )
    assert cleared.json()["preferences"] == {}

    rejected = client.put(
        "/api/v1/memory",
        headers=ALICE,
        json={"preferences": {"contact_channel": "alice@example.com"}},
    )
    assert rejected.status_code == 422
    assert "contact_channel must be one of" in rejected.json()["error"]["message"]


def test_sender_refuses_an_unconfirmed_notice(app: FastAPI) -> None:
    """A row that exists without the customer's yes must never reach a transport."""
    client = TestClient(app)
    _opt_in(client, ALICE)
    notification_id = _emit(client, "alice", "ORD-DEMO-1001")

    transport = SpyTransport()
    with app.state.session_factory() as session:
        OutboxService(session).enqueue(
            identity=Identity(tenant_id="demo", user_id="alice", roles=frozenset()),
            aggregate_type="notification",
            aggregate_id=notification_id,
            event_type="proactive.confirmed",
            dedupe_key=f"proactive:manual:{notification_id}",
            payload={"notification_id": notification_id},
        )
        session.commit()
        report = OutboxService(session).deliver(
            ProactiveSender(session, app.state.settings, transport=transport).deliver
        )

    assert report.sent == 0
    assert report.failed == 1
    assert transport.sent == []
    with app.state.session_factory() as session:
        note = session.get(Notification, notification_id)
        assert note is not None
        assert note.status == NotificationStatus.PENDING_CONFIRMATION.value
