from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.channels.base import InboundRequest
from app.channels.render import render_response, truncate_sentences
from app.core.security import Identity
from app.models.channel_message import ChannelMessage
from app.models.enums import Channel, ChannelMessageStatus
from tests.conftest import ALICE, CAROL

SECRET = "S3cr3t!"
EMAIL_MAP = {"alice@example.com": "demo:alice"}
IM_MAP = {"zhangsan": "demo:alice"}
SEAT = {"X-Tenant-Id": "demo", "X-User-Id": "seat", "X-Roles": "support"}


def _channels_app(app_factory: Callable[..., FastAPI]) -> FastAPI:
    return app_factory(
        channel_email_enabled=True,
        channel_im_enabled=True,
        channel_email_secret=SECRET,
        channel_im_secret=SECRET,
        channel_email_identity_map=EMAIL_MAP,
        channel_im_identity_map=IM_MAP,
    )


def _signed(body: bytes, *, timestamp: int | None = None, secret: str = SECRET) -> dict[str, str]:
    sent_at = str(timestamp if timestamp is not None else int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"),
        sent_at.encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Channel-Timestamp": sent_at,
        "X-Channel-Signature": f"sha256={signature}",
    }


def _post_webhook(
    client: TestClient,
    channel: str,
    payload: dict[str, Any],
    **sign_args: Any,
) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return client.post(
        f"/api/v1/channels/{channel}/webhook",
        content=body,
        headers=_signed(body, **sign_args),
    )


def _email_payload(message_id: str, text: str, **extra: Any) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "from": "alice@example.com",
        "subject": "售后咨询",
        "text": text,
        **extra,
    }


def _im_payload(message_id: str, text: str, **extra: Any) -> dict[str, Any]:
    return {
        "msg_id": message_id,
        "from_user": "zhangsan",
        "text": text,
        **extra,
    }


def _delivery_count(app: FastAPI, message_id: str) -> int:
    with app.state.session_factory() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(ChannelMessage)
                .where(ChannelMessage.channel_message_id == message_id)
            )
            or 0
        )


def _delivery_rows(app: FastAPI) -> int:
    with app.state.session_factory() as session:
        return int(
            session.scalar(select(func.count()).select_from(ChannelMessage)) or 0
        )


def test_web_channel_matches_existing_support_route(client: TestClient) -> None:
    payload = {"message": "订单 ORD-DEMO-1001 买的是什么商品？"}

    direct = client.post("/api/v1/support/messages", headers=ALICE, json=payload)
    wrapped = client.post("/api/v1/channels/web/messages", headers=ALICE, json=payload)

    assert direct.status_code == 200, direct.text
    assert wrapped.status_code == 200, wrapped.text
    assert wrapped.headers.get("X-Channel-Deduplicated") is None
    direct_body = direct.json()
    wrapped_body = wrapped.json()
    for key in ("reply", "intent", "context_cards", "clarification", "handoff"):
        assert wrapped_body[key] == direct_body[key]


def test_web_duplicate_delivery_runs_the_turn_once(
    app: FastAPI, client: TestClient
) -> None:
    headers = {**ALICE, "X-Channel-Message-Id": f"web-{uuid4().hex}"}
    payload = {"message": "帮我创建一个工单"}

    first = client.post("/api/v1/channels/web/messages", headers=headers, json=payload)
    second = client.post("/api/v1/channels/web/messages", headers=headers, json=payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.headers["X-Channel-Deduplicated"] == "true"
    assert second.json()["conversation_id"] == first.json()["conversation_id"]
    assert second.json()["task"]["task_id"] == first.json()["task"]["task_id"]
    assert client.get("/api/v1/tasks", headers=ALICE).json()["total"] == 1
    assert _delivery_count(app, headers["X-Channel-Message-Id"]) == 1


def test_reused_web_message_id_with_different_content_conflicts(
    client: TestClient,
) -> None:
    headers = {**ALICE, "X-Channel-Message-Id": f"web-{uuid4().hex}"}

    first = client.post(
        "/api/v1/channels/web/messages",
        headers=headers,
        json={"message": "订单 ORD-DEMO-1001 买的是什么商品？"},
    )
    second = client.post(
        "/api/v1/channels/web/messages",
        headers=headers,
        json={"message": "帮我创建一个工单"},
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CHANNEL_MESSAGE_ID_REUSED"


def test_support_entry_dedupes_when_given_a_delivery_id(
    app: FastAPI, client: TestClient
) -> None:
    headers = {**ALICE, "X-Channel-Message-Id": f"web-{uuid4().hex}"}
    payload = {"message": "帮我创建一个工单"}

    first = client.post("/api/v1/support/messages", headers=headers, json=payload)
    second = client.post("/api/v1/support/messages", headers=headers, json=payload)

    assert first.status_code == 200, first.text
    assert second.headers["X-Channel-Deduplicated"] == "true"
    assert second.json()["conversation_id"] == first.json()["conversation_id"]
    assert second.json()["task"]["task_id"] == first.json()["task"]["task_id"]
    assert client.get("/api/v1/tasks", headers=ALICE).json()["total"] == 1
    assert _delivery_rows(app) == 1


def test_support_entry_without_a_delivery_id_records_nothing(
    app: FastAPI, client: TestClient
) -> None:
    payload = {"message": "帮我创建一个工单"}

    for _ in range(2):
        response = client.post(
            "/api/v1/support/messages", headers=ALICE, json=payload
        )
        assert response.status_code == 200, response.text
        assert response.headers.get("X-Channel-Deduplicated") is None

    assert client.get("/api/v1/tasks", headers=ALICE).json()["total"] == 2
    assert _delivery_rows(app) == 0


def test_one_delivery_id_is_one_guard_across_both_web_entries(
    app: FastAPI, client: TestClient
) -> None:
    headers = {**ALICE, "X-Channel-Message-Id": f"web-{uuid4().hex}"}
    payload = {"message": "帮我创建一个工单"}

    channel_route = client.post(
        "/api/v1/channels/web/messages", headers=headers, json=payload
    )
    legacy_route = client.post(
        "/api/v1/support/messages", headers=headers, json=payload
    )

    assert channel_route.status_code == 200, channel_route.text
    assert legacy_route.headers["X-Channel-Deduplicated"] == "true"
    assert (
        legacy_route.json()["task"]["task_id"]
        == channel_route.json()["task"]["task_id"]
    )
    assert client.get("/api/v1/tasks", headers=ALICE).json()["total"] == 1
    assert _delivery_rows(app) == 1


def test_support_entry_rejects_a_reused_delivery_id_with_other_content(
    client: TestClient,
) -> None:
    headers = {**ALICE, "X-Channel-Message-Id": f"web-{uuid4().hex}"}

    first = client.post(
        "/api/v1/support/messages",
        headers=headers,
        json={"message": "订单 ORD-DEMO-1001 买的是什么商品？"},
    )
    second = client.post(
        "/api/v1/support/messages",
        headers=headers,
        json={"message": "帮我创建一个工单"},
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CHANNEL_MESSAGE_ID_REUSED"


def _claim_web_delivery(app: FastAPI, message_id: str, text: str) -> None:
    """Claim one web delivery through the real store and leave it PROCESSING.

    The envelope is normalised by the adapter itself, so the request digest
    matches what the HTTP route would compute for the same delivery.
    """
    gateway = app.state.channel_gateway
    adapter = gateway.adapters[Channel.WEB]
    envelope = adapter.normalize(
        InboundRequest(
            headers={"x-channel-message-id": message_id},
            body=json.dumps({"message": text}).encode("utf-8"),
            identity=Identity(tenant_id="demo", user_id="alice", roles=frozenset()),
        )
    )
    _, created = gateway.store.claim(envelope)
    assert created


def test_delivery_still_in_flight_conflicts_instead_of_running_twice(
    app: FastAPI, client: TestClient
) -> None:
    message_id = f"web-{uuid4().hex}"
    payload = {"message": "帮我创建一个工单"}
    _claim_web_delivery(app, message_id, payload["message"])

    response = client.post(
        "/api/v1/channels/web/messages",
        headers={**ALICE, "X-Channel-Message-Id": message_id},
        json=payload,
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "CHANNEL_MESSAGE_IN_FLIGHT"
    assert response.json()["error"]["retryable"] is True
    assert client.get("/api/v1/tasks", headers=ALICE).json()["total"] == 0
    assert _delivery_count(app, message_id) == 1


def test_a_stale_claim_is_taken_over_and_completed(
    app_factory: Callable[..., FastAPI]
) -> None:
    app = app_factory(channel_delivery_claim_timeout_seconds=0)
    client = TestClient(app)
    message_id = f"web-{uuid4().hex}"
    payload = {"message": "帮我创建一个工单"}
    _claim_web_delivery(app, message_id, payload["message"])

    response = client.post(
        "/api/v1/support/messages",
        headers={**ALICE, "X-Channel-Message-Id": message_id},
        json=payload,
    )

    assert response.status_code == 200, response.text
    with app.state.session_factory() as session:
        row = session.scalars(
            select(ChannelMessage).where(
                ChannelMessage.channel_message_id == message_id
            )
        ).one()
    assert row.status == ChannelMessageStatus.COMPLETED.value
    assert row.attempts == 2


def test_email_webhook_rejects_missing_or_wrong_signature(
    app_factory: Callable[..., FastAPI],
) -> None:
    client = TestClient(_channels_app(app_factory))
    body = json.dumps(_email_payload("em-unsigned", "订单状态")).encode("utf-8")

    unsigned = client.post(
        "/api/v1/channels/email/webhook",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    wrong = client.post(
        "/api/v1/channels/email/webhook",
        content=body,
        headers=_signed(body, secret="wrong-secret"),
    )
    expired = client.post(
        "/api/v1/channels/email/webhook",
        content=body,
        headers=_signed(body, timestamp=int(time.time()) - 3600),
    )

    assert unsigned.status_code == 401
    assert wrong.status_code == 401
    assert expired.status_code == 401
    assert unsigned.json()["error"]["code"] == "UNAUTHORIZED"


def test_email_reply_is_formal_and_duplicate_delivery_replies_once(
    app_factory: Callable[..., FastAPI],
) -> None:
    application = _channels_app(app_factory)
    client = TestClient(application)
    message_id = f"em-{uuid4().hex}"
    payload = _email_payload(message_id, "帮我创建一个工单")

    first = _post_webhook(client, "email", payload)
    second = _post_webhook(client, "email", payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    body = first.json()
    assert body["channel"] == "EMAIL"
    assert body["intent"] == "TICKET_CREATE"
    assert body["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    assert second.json()["text"] == body["text"]
    assert "受理编号：TCK-" in body["text"]
    assert "处理时限：24 小时内首次回复" in body["text"]
    assert "TicketPilot 客服团队" in body["text"]
    assert "此邮件由系统自动发送" in body["text"]
    assert body["subject"].startswith("TicketPilot 售后回复")
    assert _delivery_count(application, message_id) == 1
    listed = client.get("/api/v1/tasks", headers=ALICE).json()
    assert listed["total"] == 1
    task_id = listed["items"][0]["task_id"]
    detail = client.get(f"/api/v1/tasks/{task_id}", headers=ALICE)
    assert detail.status_code == 200, detail.text
    assert detail.json()["context"]["channel"] == Channel.EMAIL.value


def test_email_unknown_sender_is_rejected(
    app_factory: Callable[..., FastAPI],
) -> None:
    client = TestClient(_channels_app(app_factory))
    payload = _email_payload(f"em-{uuid4().hex}", "订单状态")
    payload["from"] = "mallory@example.com"

    response = _post_webhook(client, "email", payload)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_im_reply_is_capped_and_cards_become_a_reply_index(
    app_factory: Callable[..., FastAPI],
) -> None:
    client = TestClient(_channels_app(app_factory))
    payload = _im_payload(
        f"im-{uuid4().hex}",
        "订单 ORD-DEMO-1001 买的是什么商品？",
    )

    response = _post_webhook(client, "wecom", payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["channel"] == "WECOM"
    assert body["intent"] == "PRODUCT_QUERY"
    assert len(body["text"]) <= 200
    assert "详情：" in body["text"]
    assert "1." in body["text"]


def test_im_identity_comes_from_config_not_the_payload(
    app_factory: Callable[..., FastAPI],
) -> None:
    application = _channels_app(app_factory)
    client = TestClient(application)
    message_id = f"im-{uuid4().hex}"
    payload = _im_payload(
        message_id,
        "帮我创建一个工单",
        tenant_id="acme",
        user_id="carol",
    )

    response = _post_webhook(client, "wechat", payload)

    assert response.status_code == 200, response.text
    assert client.get("/api/v1/tasks", headers=ALICE).json()["total"] == 1
    assert client.get("/api/v1/tasks", headers=CAROL).json()["total"] == 0
    assert _delivery_count(application, message_id) == 1


def test_disabled_or_unknown_channel_is_not_exposed(
    app_factory: Callable[..., FastAPI],
) -> None:
    client = TestClient(app_factory())
    body = json.dumps(_im_payload("im-disabled", "你好")).encode("utf-8")

    disabled = client.post(
        "/api/v1/channels/wecom/webhook",
        content=body,
        headers=_signed(body),
    )
    unknown = client.post(
        "/api/v1/channels/carrier-pigeon/webhook",
        content=body,
        headers=_signed(body),
    )

    assert disabled.status_code == 404
    assert unknown.status_code == 404


def test_matrix_publishes_capabilities(client: TestClient) -> None:
    response = client.get("/api/v1/channels", headers=ALICE)

    assert response.status_code == 200, response.text
    channels = {item["channel"]: item for item in response.json()["channels"]}
    assert channels["WECHAT"]["max_length"] == 200
    assert channels["WECOM"]["supports_rich_card"] is False
    assert channels["EMAIL"]["formal"] is True
    assert channels["VOICE"]["offers_human_handoff"] is True
    assert channels["WEB"]["supports_stream"] is True
    assert channels["WEB"]["deliveries"] == {}
    assert channels["APP"]["enabled"] is False
    assert channels["WHATSAPP"]["enabled"] is False


def test_matrix_counts_recorded_deliveries_per_channel(
    app_factory: Callable[..., FastAPI],
) -> None:
    application = _channels_app(app_factory)
    client = TestClient(application)
    message_id = f"em-{uuid4().hex}"

    inbound = _post_webhook(
        client, "email", _email_payload(message_id, "查一下物流")
    )
    assert inbound.status_code == 200, inbound.text

    channels = {
        item["channel"]: item
        for item in client.get("/api/v1/channels", headers=ALICE).json()["channels"]
    }
    assert channels["EMAIL"]["deliveries"] == {"COMPLETED": 1}
    assert channels["WEB"]["deliveries"] == {}


def test_handoff_bundle_keeps_the_channel_it_arrived_on(
    app_factory: Callable[..., FastAPI],
) -> None:
    """Provenance is data: an email case must not look like a web case."""
    client = TestClient(_channels_app(app_factory))

    webhook = _post_webhook(
        client, "email", _email_payload(f"em-{uuid4().hex}", "转人工，我要投诉")
    )
    web = client.post(
        "/api/v1/support/messages", headers=ALICE, json={"message": "转人工"}
    )

    assert webhook.status_code == 200, webhook.text
    assert web.status_code == 200, web.text

    items = client.get("/api/v1/handoffs", headers=SEAT).json()["items"]
    assert sorted(item["channel"] for item in items) == ["EMAIL", "WEB"]
    first = client.get(f"/api/v1/handoffs/{items[0]['event_id']}", headers=SEAT).json()
    assert first["bundle"]["channel"] == items[0]["channel"]


def test_renderer_truncates_at_sentence_boundary() -> None:
    text, truncated = truncate_sentences("第一句话。第二句话。第三句话。", 12)

    assert truncated is True
    assert len(text) <= 12
    assert text.endswith("…")
    assert "第三句话" not in text


def test_voice_and_sms_shapes() -> None:
    response: dict[str, Any] = {
        "reply": "退款会在三个工作日到账。详情见 https://example.com/policy。",
        "conversation_id": "conv-1",
        "context_cards": [],
    }

    voice = render_response(Channel.VOICE, response)
    sms = render_response(Channel.SMS, response)

    assert "https://" not in voice.text
    assert "转人工" in voice.text
    assert len(sms.text) <= 70
    assert sms.text.endswith("回复TD退订")
