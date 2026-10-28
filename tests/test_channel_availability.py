"""Inbound availability of every channel: four values, one source of truth.

Spec: openspec/changes/add-channel-availability-reason/specs/channel-inbound/spec.md
Plan: openspec/changes/add-channel-availability-reason/plan.md (tasks 1-4)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import types
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.channels import gateway
from app.channels.availability import AVAILABILITY_VALUES, inbound_availability
from app.channels.registry import INBOUND_IMPLEMENTED, WEBHOOK_INBOUND, build_adapters
from app.channels.render import capabilities_for
from app.channels.web import WebChannelAdapter
from app.core.config import Settings
from app.models.enums import Channel
from app.schemas.channels import ChannelCapabilityView, ChannelMatrixResponse
from tests.conftest import ALICE


def _stub(secret: Any) -> Any:
    """A minimal adapter stub; the only attribute the predicate reads."""
    return types.SimpleNamespace(secret=secret)


def test_availability_decision_order_matches_facts() -> None:
    cases: list[tuple[Channel, Any, str]] = [
        # 1. no inbound path at all wins over everything else ...
        (Channel.APP, None, "not_implemented"),
        # ... even when an adapter object exists, so the order cannot be shuffled.
        (Channel.APP, _stub(SecretStr("k")), "not_implemented"),
        (Channel.WHATSAPP, _stub(SecretStr("k")), "not_implemented"),
        (Channel.VOICE, None, "not_implemented"),
        (Channel.SMS, None, "not_implemented"),
        # 2. implemented but not built (switch off).
        (Channel.EMAIL, None, "not_enabled"),
        (Channel.WECHAT, None, "not_enabled"),
        (Channel.WECOM, None, "not_enabled"),
        # 3. built, but the signing secret the adapter actually verifies with is empty.
        (Channel.EMAIL, _stub(None), "webhook_secret_missing"),
        (Channel.EMAIL, _stub(SecretStr("")), "webhook_secret_missing"),
        (Channel.WECHAT, _stub(None), "webhook_secret_missing"),
        (Channel.WECOM, _stub(None), "webhook_secret_missing"),
        # 4. everything else.
        (Channel.EMAIL, _stub(SecretStr("k")), "available"),
        (Channel.WECHAT, _stub(SecretStr("k")), "available"),
        (Channel.WECOM, _stub(SecretStr("k")), "available"),
    ]

    for channel, adapter, expected in cases:
        assert inbound_availability(channel, adapter) == expected, channel

    # The console channel has no signing secret at all and must never be
    # mistaken for a credential problem.
    web = WebChannelAdapter()
    assert not hasattr(web, "secret")
    assert inbound_availability(Channel.WEB, web) == "available"
    assert inbound_availability(Channel.WEB, _stub(None)) == "available"

    assert len(AVAILABILITY_VALUES) == 4
    for channel, adapter, _expected in cases:
        assert inbound_availability(channel, adapter) in AVAILABILITY_VALUES


def test_webhook_channels_are_derived_from_inbound_set() -> None:
    assert gateway._WEBHOOK_CHANNELS == WEBHOOK_INBOUND
    assert WEBHOOK_INBOUND == INBOUND_IMPLEMENTED - {Channel.WEB}
    assert Channel.WEB not in WEBHOOK_INBOUND


# Expected under the default development configuration: web on, email/IM off.
DEFAULT_EXPECTED = {
    "WEB": "available",
    "EMAIL": "not_enabled",
    "WECHAT": "not_enabled",
    "WECOM": "not_enabled",
    "APP": "not_implemented",
    "WHATSAPP": "not_implemented",
    "VOICE": "not_implemented",
    "SMS": "not_implemented",
}


def _matrix(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/v1/channels", headers=ALICE)
    assert response.status_code == 200, response.text
    return {item["channel"]: item for item in response.json()["channels"]}


def test_matrix_gives_every_channel_an_availability_value(client: TestClient) -> None:
    channels = _matrix(client)

    assert set(channels) == {channel.value for channel in Channel}
    for name, expected in DEFAULT_EXPECTED.items():
        assert channels[name]["availability"] == expected, name

    seen = {item["availability"] for item in channels.values()}
    assert seen <= set(AVAILABILITY_VALUES), seen
    assert len(AVAILABILITY_VALUES) == 4

    # A value outside the closed set must never reach the response: the
    # response validation rejects it. The valid payload is validated first, so
    # the rejections below can only be caused by the out-of-set value.
    valid: dict[str, Any] = {
        "channel": "EMAIL",
        "enabled": True,
        "availability": "available",
        "supports_stream": False,
        "supports_rich_card": False,
        "supports_voice": False,
        "max_length": None,
        "max_cards": 0,
        "sentence_char_limit": None,
        "formal": True,
        "offers_human_handoff": False,
    }
    assert ChannelCapabilityView.model_validate(valid).availability == "available"

    for outside in ("implemented", "declared_only", "disabled_in_prod", ""):
        with pytest.raises(ValidationError):
            ChannelCapabilityView.model_validate({**valid, "availability": outside})

        with pytest.raises(ValidationError):
            ChannelMatrixResponse.model_validate(
                {"channels": [{**valid, "availability": outside}]}
            )


def test_availability_agrees_with_enabled(client: TestClient) -> None:
    for name, item in _matrix(client).items():
        assert item["enabled"] == (
            item["availability"] in {"available", "webhook_secret_missing"}
        ), name


def test_enabled_channel_with_secret_reports_available(
    app_factory: Callable[..., FastAPI],
) -> None:
    application = app_factory(
        channel_email_enabled=True,
        channel_email_secret="k",
        channel_email_identity_map={"alice@example.com": "demo:alice"},
    )
    channels = _matrix(TestClient(application))

    assert channels["EMAIL"]["availability"] == "available"
    assert channels["EMAIL"]["enabled"] is True


# --- guards: the inbound set, the webhook set and the production check are one fact ---

_SECRET = "S3cr3t!"
_SENDER_MAP = {"alice@example.com": "demo:alice"}
_IM_SENDER_MAP = {"zhangsan": "demo:alice"}

# A production configuration that passes every other startup safety check, so
# the raised message can only be about the channel credential under test.
_PROD_BASE: dict[str, Any] = {
    "environment": "prod",
    "database_url": "postgresql+psycopg://user:pw@localhost:5432/ticketpilot",
    "auto_create_tables": False,
    "identity_adapter": "oidc",
    "refund_provider": "http",
    "docs_enabled": False,
}

# (channel, switch, secret field, identity map field, expected message fragment)
_PROD_CASES = [
    (
        Channel.EMAIL,
        {"channel_email_enabled": True},
        "channel_email_secret",
        "channel_email_identity_map",
        "CHANNEL_EMAIL_SECRET",
    ),
    (
        Channel.WECHAT,
        {"channel_im_enabled": True},
        "channel_im_secret",
        "channel_im_identity_map",
        "CHANNEL_IM_SECRET",
    ),
    (
        Channel.WECOM,
        {"channel_im_enabled": True},
        "channel_im_secret",
        "channel_im_identity_map",
        "CHANNEL_IM_SECRET",
    ),
]


def _settings_with(**overrides: Any) -> Settings:
    return Settings(environment="test", **overrides)


def _signed_headers(body: bytes, secret: str = _SECRET) -> dict[str, str]:
    """The same signing scheme the providers use (see tests/test_channels.py)."""
    sent_at = str(int(time.time()))
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


def test_inbound_declared_set_equals_adapters_with_all_flags_on() -> None:
    settings = _settings_with(
        channel_web_enabled=True,
        channel_email_enabled=True,
        channel_email_secret=_SECRET,
        channel_email_identity_map=_SENDER_MAP,
        channel_im_enabled=True,
        channel_im_secret=_SECRET,
        channel_im_identity_map=_IM_SENDER_MAP,
    )
    adapters = build_adapters(settings)

    assert set(adapters) == set(INBOUND_IMPLEMENTED)
    # One IM switch still builds two distinct channel instances.
    assert adapters[Channel.WECHAT] is not adapters[Channel.WECOM]


def test_webhook_inbound_set_is_inbound_minus_web() -> None:
    assert WEBHOOK_INBOUND == INBOUND_IMPLEMENTED - {Channel.WEB}
    assert Channel.WEB not in WEBHOOK_INBOUND


@pytest.mark.parametrize(
    ("channel", "switch", "secret_field", "map_field", "needle"), _PROD_CASES
)
def test_prod_safety_rejects_exactly_when_availability_is_webhook_secret_missing(
    channel: Channel,
    switch: dict[str, Any],
    secret_field: str,
    map_field: str,
    needle: str,
) -> None:
    """Bidirectional: empty secret == prod refuses to start == non-prod reports the reason."""
    empty = {**switch, secret_field: None, map_field: dict(_SENDER_MAP)}

    with pytest.raises(ValueError, match=needle):
        Settings(**_PROD_BASE, **empty)

    missing_settings = _settings_with(**empty)
    missing_adapter = build_adapters(missing_settings).get(channel)
    assert inbound_availability(channel, missing_adapter) == "webhook_secret_missing"

    filled = {**switch, secret_field: _SECRET, map_field: dict(_SENDER_MAP)}
    assert Settings(**_PROD_BASE, **filled) is not None

    ready_settings = _settings_with(**filled)
    ready_adapter = build_adapters(ready_settings).get(channel)
    assert inbound_availability(channel, ready_adapter) == "available"


def test_webhook_returns_503_exactly_when_availability_is_webhook_secret_missing(
    app_factory: Callable[..., FastAPI],
) -> None:
    """The 503 a missing webhook secret actually produces, pinned for the first time."""
    application = app_factory(
        channel_email_enabled=True,
        channel_email_secret=None,
        channel_email_identity_map=_SENDER_MAP,
    )
    client = TestClient(application)

    assert _matrix(client)["EMAIL"]["availability"] == "webhook_secret_missing"

    body = json.dumps(
        {
            "message_id": "em-missing-secret",
            "from": "alice@example.com",
            "subject": "售后咨询",
            "text": "查一下物流",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    rejected = client.post(
        "/api/v1/channels/email/webhook", content=body, headers=_signed_headers(body)
    )

    assert rejected.status_code == 503, rejected.text
    error = rejected.json()["error"]
    assert error["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "webhook secret" in error["message"]

    ready_app = app_factory(
        channel_email_enabled=True,
        channel_email_secret=_SECRET,
        channel_email_identity_map=_SENDER_MAP,
    )
    ready_client = TestClient(ready_app)
    assert _matrix(ready_client)["EMAIL"]["availability"] == "available"
    accepted = ready_client.post(
        "/api/v1/channels/email/webhook", content=body, headers=_signed_headers(body)
    )
    assert accepted.status_code == 200, accepted.text


_CAPABILITY_FIELDS = (
    "supports_stream",
    "supports_rich_card",
    "supports_voice",
    "max_length",
    "max_cards",
    "sentence_char_limit",
    "formal",
    "offers_human_handoff",
)


def test_existing_capability_assertions_unchanged(client: TestClient) -> None:
    """Adding availability must not squeeze a single capability value."""
    channels = _matrix(client)

    for channel in Channel:
        item = channels[channel.value]
        capabilities = capabilities_for(channel)
        for field in _CAPABILITY_FIELDS:
            assert item[field] == getattr(capabilities, field), (channel, field)
        assert item["channel"] == channel.value

    # The response shape is the view model's shape: names, order and the one
    # added field, with nothing dropped or renamed.
    for channel in Channel:
        assert list(channels[channel.value]) == list(ChannelCapabilityView.model_fields)
