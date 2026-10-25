"""G10: a seat changes what a proactive notification says, without a redeploy.

The rule table and its read endpoint existed; what was missing was any way to
change the words short of editing the seed and re-running it. These cases hold
the two halves of that: the write surface refuses the template shapes that
used to fail *later and silently* (an unknown key suppresses the rule forever,
a positional one raises during delivery), and a change reaches only the next
notification — the rows already waiting for confirmation are not rewritten.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.memory import ProactiveRule
from tests.conftest import CAROL

SUPPORT = {"X-Tenant-Id": "demo", "X-User-Id": "agent-1", "X-Roles": "support"}
SUPPORT_ACME = {"X-Tenant-Id": "acme", "X-User-Id": "agent-2", "X-Roles": "support"}
REFUND = "REFUND_SETTLED"
SEED_TAIL = "请注意查收"
NEW_TEMPLATE = "退款 {amount} 元已到账，订单 {order_id}，有问题直接回复这条消息。"
MARKER = "MARKER-ONLY-IN-REQUEST-BODY"
ALICE = {"X-Tenant-Id": "demo", "X-User-Id": "alice"}


def _rules(client: TestClient, headers: dict[str, str] = SUPPORT) -> list[dict[str, Any]]:
    response = client.get("/api/v1/memory/rules", headers=headers)
    assert response.status_code == 200
    return list(response.json()["rules"])


def _refund_rule(client: TestClient, headers: dict[str, str] = SUPPORT) -> dict[str, Any]:
    return next(rule for rule in _rules(client, headers) if rule["event_type"] == REFUND)


def _put(
    client: TestClient,
    headers: dict[str, str],
    event_type: str = REFUND,
    **changes: Any,
) -> Any:
    return client.put(
        f"/api/v1/memory/rules/{event_type}", headers=headers, json=changes
    )


def _opt_in(client: TestClient, headers: dict[str, str] = ALICE) -> None:
    response = client.put(
        "/api/v1/memory", headers=headers, json={"proactive_opt_in": True}
    )
    assert response.status_code == 200


def _emit(
    client: TestClient,
    headers: dict[str, str],
    *,
    user_id: str = "alice",
    event_key: str = "ORD-T10-0001",
) -> Any:
    response = client.post(
        "/api/v1/memory/events",
        headers=headers,
        json={
            "user_id": user_id,
            "event_type": REFUND,
            "event_key": event_key,
            "context": {"order_id": "ORD-T10-0001", "amount": "129.00"},
        },
    )
    assert response.status_code == 200
    return response.json()


def _audit_rows(app: FastAPI, action: str) -> list[AuditEvent]:
    with app.state.session_factory() as session:
        return list(
            session.scalars(select(AuditEvent).where(AuditEvent.action == action)).all()
        )


def _rule_rows(app: FastAPI, tenant_id: str) -> list[ProactiveRule]:
    with app.state.session_factory() as session:
        return list(
            session.scalars(
                select(ProactiveRule).where(ProactiveRule.tenant_id == tenant_id)
            ).all()
        )


def test_seat_rewrites_template_and_view_reports_its_inputs(client: TestClient) -> None:
    before = _refund_rule(client)
    assert before["required_context"] == ["amount", "order_id"]

    response = _put(client, SUPPORT, template=NEW_TEMPLATE)
    assert response.status_code == 200
    body = response.json()
    assert body["template"] == NEW_TEMPLATE
    assert body["updated_at"] is not None
    assert body["updated_at"] != before["updated_at"]
    assert body["required_context"] == ["amount", "order_id"]
    assert body["requires_confirmation"] is True
    assert _refund_rule(client)["template"] == NEW_TEMPLATE


def test_read_only_view_lists_required_context_for_every_rule(client: TestClient) -> None:
    assert [
        (rule["event_type"], rule["required_context"]) for rule in _rules(client)
    ] == [
        ("DELIVERY_EXCEPTION", ["order_id", "summary"]),
        ("REFUND_SETTLED", ["amount", "order_id"]),
        ("TICKET_UPDATED", ["summary", "ticket_no"]),
    ]


def test_customer_cannot_write_a_rule(client: TestClient) -> None:
    before = _refund_rule(client)
    assert _put(client, ALICE, template=NEW_TEMPLATE).status_code == 403
    assert _refund_rule(client) == before


def test_unknown_event_type_is_rejected_with_the_allowed_list(client: TestClient) -> None:
    response = _put(client, SUPPORT, "PACKAGE_LOST", template="包裹 {order_id} 已发出。")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "UNKNOWN_PROACTIVE_EVENT"
    assert error["details"]["allowed"] == [
        "DELIVERY_EXCEPTION",
        "REFUND_SETTLED",
        "TICKET_UPDATED",
    ]


def test_template_outside_the_context_allowlist_leaves_the_row_untouched(
    client: TestClient,
) -> None:
    before = _refund_rule(client)
    response = _put(client, SUPPORT, template="您的发票 {invoice_no} 已开出。")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "RULE_TEMPLATE_UNKNOWN_KEY"
    assert "invoice_no" in error["message"]
    assert "allowed" in error["details"]
    assert _refund_rule(client) == before


def test_positional_and_attribute_placeholders_are_rejected(client: TestClient) -> None:
    # ``{}`` is the shape that used to raise inside emit and surface as a 500.
    for template in ("订单 {} 已退款。", "{order_id.upper} 已退款。", "{amount[0]} 已退款。"):
        response = _put(client, SUPPORT, template=template)
        assert response.status_code == 422, template
        assert response.json()["error"]["code"] == "RULE_TEMPLATE_INVALID", template


def test_unbalanced_brace_is_rejected(client: TestClient) -> None:
    response = _put(client, SUPPORT, template="订单 {order_id 已退款。")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "RULE_TEMPLATE_INVALID"


def test_template_longer_than_the_render_budget_is_rejected(client: TestClient) -> None:
    # emit truncates the rendered body at 500 characters, so a template that
    # only just fits would silently lose the tail of its own sentence.
    too_long = "退款已到账。" * 68 + "{amount}"
    assert len(too_long) > 400
    response = _put(client, SUPPORT, template=too_long)
    assert response.status_code == 422
    assert "400" in response.json()["error"]["message"]

    fits = "退款 {amount} 元已到账。" + "详询客服。" * 30
    assert len(fits) <= 400
    assert _put(client, SUPPORT, template=fits).status_code == 200


def test_empty_template_is_rejected(client: TestClient) -> None:
    assert _put(client, SUPPORT, template="   ").status_code == 422


def test_confirmation_gate_is_not_writable(client: TestClient) -> None:
    before = _refund_rule(client)
    assert _put(client, SUPPORT, requires_confirmation=False).status_code == 422
    after = _refund_rule(client)
    assert after == before
    assert after["requires_confirmation"] is True


def test_cooldown_is_bounded(client: TestClient) -> None:
    before = _refund_rule(client)
    assert _put(client, SUPPORT, cooldown_minutes=-1).status_code == 422
    assert _put(client, SUPPORT, cooldown_minutes=1441).status_code == 422
    assert _refund_rule(client) == before
    assert _put(client, SUPPORT, cooldown_minutes=15).status_code == 200
    assert _refund_rule(client)["cooldown_minutes"] == 15


def test_change_reaches_the_next_notification_but_not_the_pending_one(
    client: TestClient,
) -> None:
    _opt_in(client)
    first = _emit(client, SUPPORT)
    assert first["status"] == "PENDING_CONFIRMATION"
    assert SEED_TAIL in first["notification"]["body"]

    # cooldown 0 so the second event key is not suppressed for the wrong reason
    assert (
        _put(client, SUPPORT, template=NEW_TEMPLATE, cooldown_minutes=0).status_code
        == 200
    )

    listed = client.get("/api/v1/memory/notifications", headers=ALICE).json()
    pending = listed["notifications"][0]
    assert pending["notification_id"] == first["notification"]["notification_id"]
    assert pending["body"] == first["notification"]["body"]

    second = _emit(client, SUPPORT, event_key="ORD-T10-0002")
    assert (
        second["notification"]["body"]
        == "退款 129.00 元已到账，订单 ORD-T10-0001，有问题直接回复这条消息。"
    )
    assert SEED_TAIL not in second["notification"]["body"]


def test_disabling_a_rule_stops_emitting_and_re_enabling_restores_it(
    client: TestClient,
) -> None:
    _opt_in(client)
    assert _put(client, SUPPORT, enabled=False).status_code == 200
    suppressed = _emit(client, SUPPORT)
    assert suppressed["status"] == "SUPPRESSED"
    assert suppressed["reason"] == "NO_RULE"

    assert _put(client, SUPPORT, enabled=True).status_code == 200
    assert _emit(client, SUPPORT)["status"] == "PENDING_CONFIRMATION"


def test_zero_cooldown_lets_the_next_event_through_immediately(
    client: TestClient,
) -> None:
    _opt_in(client)
    assert _emit(client, SUPPORT)["status"] == "PENDING_CONFIRMATION"
    assert _emit(client, SUPPORT, event_key="ORD-T10-0002")["reason"] == "COOLDOWN"

    assert _put(client, SUPPORT, cooldown_minutes=0).status_code == 200
    assert (
        _emit(client, SUPPORT, event_key="ORD-T10-0003")["status"]
        == "PENDING_CONFIRMATION"
    )


def test_audit_holds_a_digest_of_the_words_not_the_words(
    app: FastAPI, client: TestClient
) -> None:
    template = f"退款已到账，{MARKER}，订单 {{order_id}}。"
    assert (
        _put(client, SUPPORT, template=template, cooldown_minutes=5).status_code == 200
    )

    rows = _audit_rows(app, "proactive.rule.update")
    assert len(rows) == 1
    row = rows[0]
    assert row.resource_type == "proactive_rule"
    assert row.resource_id == "rule_demo_refund_settled"
    assert row.actor_id == "agent-1"
    assert row.detail == {
        "event_type": REFUND,
        "fields": ["cooldown_minutes", "template"],
        "enabled": True,
        "cooldown_minutes": 5,
        "template_sha256": hashlib.sha256(template.encode("utf-8")).hexdigest(),
        "created": False,
    }
    assert MARKER not in json.dumps(row.detail, ensure_ascii=False)


def test_a_tenant_the_seed_never_covered_can_turn_it_on(client: TestClient) -> None:
    assert _rules(client, SUPPORT_ACME) == []

    response = _put(
        client,
        SUPPORT_ACME,
        template="您的订单 {order_id} 退款 {amount} 元已到账。",
        cooldown_minutes=30,
    )
    assert response.status_code == 200
    created = response.json()
    assert created["rule_id"] == "rule_acme_refund_settled"
    assert created["enabled"] is True
    assert created["requires_confirmation"] is True
    assert created["cooldown_minutes"] == 30

    _opt_in(client, CAROL)
    emitted = _emit(client, SUPPORT_ACME, user_id="carol")
    assert emitted["status"] == "PENDING_CONFIRMATION"
    assert (
        emitted["notification"]["body"]
        == "您的订单 ORD-T10-0001 退款 129.00 元已到账。"
    )


def test_new_rule_is_on_by_default_and_can_open_disabled(client: TestClient) -> None:
    assert _rules(client, SUPPORT_ACME) == []
    response = _put(
        client, SUPPORT_ACME, enabled=False, template="订单 {order_id} 有进展。"
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["cooldown_minutes"] == 60
    assert response.json()["requires_confirmation"] is True


def test_new_rule_needs_a_template(client: TestClient) -> None:
    assert _rules(client, SUPPORT_ACME) == []
    response = _put(client, SUPPORT_ACME, cooldown_minutes=30)
    assert response.status_code == 422
    assert "template" in response.json()["error"]["message"]
    assert _rules(client, SUPPORT_ACME) == []


def test_rejected_create_persists_nothing(app: FastAPI, client: TestClient) -> None:
    assert (
        _put(client, SUPPORT_ACME, template="订单 {invoice_no} 已开票。").status_code
        == 422
    )
    assert _rules(client, SUPPORT_ACME) == []
    assert _rule_rows(app, "acme") == []


def test_rules_do_not_cross_tenants(client: TestClient) -> None:
    demo_before = _rules(client)
    assert _put(client, SUPPORT_ACME, template=NEW_TEMPLATE).status_code == 200

    acme = _rules(client, SUPPORT_ACME)
    assert [rule["event_type"] for rule in acme] == [REFUND]
    assert _rules(client) == demo_before


def test_empty_change_is_rejected(client: TestClient) -> None:
    assert _put(client, SUPPORT).status_code == 422
