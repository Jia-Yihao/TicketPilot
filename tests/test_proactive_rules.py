"""G10 / P4: what a rule says is versioned, and one person cannot change it.

The original surface wrote the tenant rule in place: valid template, immediate
effect, audit holding only a digest. This suite now pins the governance layer
on top of the same deterministic validation: a seat submits, another approver
decides, only an approval changes the rule, every applied state leaves an
immutable version, and a rollback is just another proposed change.

The older cases stay: bad templates are still refused before anything is
stored, and a change still reaches only the next notification.
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
from tests.conftest import CAROL, REVIEWER

SUPPORT = {"X-Tenant-Id": "demo", "X-User-Id": "agent-1", "X-Roles": "support"}
SUPPORT_SELF_APPROVER = {
    "X-Tenant-Id": "demo",
    "X-User-Id": "agent-1",
    "X-Roles": "support,approver",
}
SUPPORT_OTHER = {"X-Tenant-Id": "demo", "X-User-Id": "agent-2", "X-Roles": "support"}
APPROVER = {"X-Tenant-Id": "demo", "X-User-Id": "agent-9", "X-Roles": "approver"}
SUPPORT_ACME = {"X-Tenant-Id": "acme", "X-User-Id": "agent-2", "X-Roles": "support"}
APPROVER_ACME = {"X-Tenant-Id": "acme", "X-User-Id": "agent-8", "X-Roles": "approver"}
REFUND = "REFUND_SETTLED"
SEED_TAIL = "请注意查收"
SEED_TEMPLATE = "您的订单 {order_id} 退款 {amount} 元已到账，请注意查收。"
NEW_TEMPLATE = "退款 {amount} 元已到账，订单 {order_id}，有问题直接回复这条消息。"
MARKER = "MARKER-ONLY-IN-REQUEST-BODY"
ALICE = {"X-Tenant-Id": "demo", "X-User-Id": "alice"}
BOB = {"X-Tenant-Id": "demo", "X-User-Id": "bob"}


def _rules(client: TestClient, headers: dict[str, str] = SUPPORT) -> list[dict[str, Any]]:
    response = client.get("/api/v1/memory/rules", headers=headers)
    assert response.status_code == 200
    return list(response.json()["rules"])


def _refund_rule(client: TestClient, headers: dict[str, str] = SUPPORT) -> dict[str, Any]:
    return next(rule for rule in _rules(client, headers) if rule["event_type"] == REFUND)


def _submit(
    client: TestClient,
    headers: dict[str, str] = SUPPORT,
    event_type: str = REFUND,
    **changes: Any,
) -> Any:
    return client.put(
        f"/api/v1/memory/rules/{event_type}", headers=headers, json=changes
    )


def _approve(
    client: TestClient,
    change_id: str,
    headers: dict[str, str] = APPROVER,
    **body: Any,
) -> Any:
    return client.post(
        f"/api/v1/memory/rules/changes/{change_id}/approve",
        headers=headers,
        json=body,
    )


def _reject(
    client: TestClient,
    change_id: str,
    headers: dict[str, str] = APPROVER,
    **body: Any,
) -> Any:
    return client.post(
        f"/api/v1/memory/rules/changes/{change_id}/reject",
        headers=headers,
        json=body,
    )


def _rollback(
    client: TestClient,
    version: int,
    headers: dict[str, str] = SUPPORT,
    event_type: str = REFUND,
    **body: Any,
) -> Any:
    return client.post(
        f"/api/v1/memory/rules/{event_type}/rollback",
        headers=headers,
        json={"version": version, **body},
    )


def _apply(
    client: TestClient,
    headers: dict[str, str] = SUPPORT,
    approver: dict[str, str] = APPROVER,
    event_type: str = REFUND,
    **changes: Any,
) -> dict[str, Any]:
    submitted = _submit(client, headers, event_type, **changes)
    assert submitted.status_code == 202, submitted.text
    approved = _approve(client, submitted.json()["change_id"], approver)
    assert approved.status_code == 200, approved.text
    return approved.json()


def _changes(
    client: TestClient,
    headers: dict[str, str] = SUPPORT,
    **params: Any,
) -> list[dict[str, Any]]:
    response = client.get("/api/v1/memory/rules/changes", headers=headers, params=params)
    assert response.status_code == 200
    return list(response.json()["changes"])


def _versions(
    client: TestClient,
    headers: dict[str, str] = SUPPORT,
    event_type: str = REFUND,
) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/v1/memory/rules/{event_type}/versions", headers=headers
    )
    assert response.status_code == 200
    return list(response.json()["versions"])


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


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_seat_submits_and_another_approver_rewrites_the_template(
    client: TestClient,
) -> None:
    before = _refund_rule(client)
    assert before["required_context"] == ["amount", "order_id"]
    assert before["version"] == 1

    submitted = _submit(client, SUPPORT, template=NEW_TEMPLATE, reason="统一口径")
    assert submitted.status_code == 202
    pending = submitted.json()
    assert pending["status"] == "PENDING"
    assert pending["kind"] == "UPDATE"
    assert pending["template"] == NEW_TEMPLATE
    assert pending["base_version"] == 1
    assert pending["submitted_by"] == "agent-1"
    assert pending["required_context"] == ["amount", "order_id"]
    assert pending["requires_confirmation"] is True

    # The seat's own view and the customer-facing outcome are still the old words.
    assert _refund_rule(client) == before

    approved = _approve(client, pending["change_id"])
    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "APPROVED"
    assert body["applied_version"] == 2
    assert body["decided_by"] == "agent-9"

    after = _refund_rule(client)
    assert after["template"] == NEW_TEMPLATE
    assert after["version"] == 2
    assert after["updated_at"] != before["updated_at"]
    assert after["required_context"] == ["amount", "order_id"]
    assert after["requires_confirmation"] is True


def test_read_only_view_lists_required_context_for_every_rule(client: TestClient) -> None:
    assert [
        (rule["event_type"], rule["required_context"]) for rule in _rules(client)
    ] == [
        ("DELIVERY_EXCEPTION", ["order_id", "summary"]),
        ("REFUND_SETTLED", ["amount", "order_id"]),
        ("TICKET_UPDATED", ["summary", "ticket_no"]),
    ]


def test_customer_cannot_write_or_read_governance(client: TestClient) -> None:
    before = _refund_rule(client)
    assert _submit(client, ALICE, template=NEW_TEMPLATE).status_code == 403
    assert client.get("/api/v1/memory/rules/changes", headers=ALICE).status_code == 403
    assert (
        client.get(
            f"/api/v1/memory/rules/{REFUND}/versions", headers=ALICE
        ).status_code
        == 403
    )
    assert client.get("/api/v1/memory/rules", headers=ALICE).status_code == 403
    assert _refund_rule(client) == before


def test_unknown_event_type_is_rejected_with_the_allowed_list(client: TestClient) -> None:
    response = _submit(client, SUPPORT, "PACKAGE_LOST", template="包裹 {order_id} 已发出。")
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
    response = _submit(client, SUPPORT, template="您的发票 {invoice_no} 已开出。")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "RULE_TEMPLATE_UNKNOWN_KEY"
    assert "invoice_no" in error["message"]
    assert "allowed" in error["details"]
    assert _refund_rule(client) == before
    assert _changes(client) == []


def test_positional_and_attribute_placeholders_are_rejected(client: TestClient) -> None:
    # ``{}`` is the shape that used to raise inside emit and surface as a 500.
    for template in ("订单 {} 已退款。", "{order_id.upper} 已退款。", "{amount[0]} 已退款。"):
        response = _submit(client, SUPPORT, template=template)
        assert response.status_code == 422, template
        assert response.json()["error"]["code"] == "RULE_TEMPLATE_INVALID", template


def test_unbalanced_brace_is_rejected(client: TestClient) -> None:
    response = _submit(client, SUPPORT, template="订单 {order_id 已退款。")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "RULE_TEMPLATE_INVALID"


def test_template_longer_than_the_render_budget_is_rejected(client: TestClient) -> None:
    # emit truncates the rendered body at 500 characters, so a template that
    # only just fits would silently lose the tail of its own sentence.
    too_long = "退款已到账。" * 68 + "{amount}"
    assert len(too_long) > 400
    response = _submit(client, SUPPORT, template=too_long)
    assert response.status_code == 422
    assert "400" in response.json()["error"]["message"]

    fits = "退款 {amount} 元已到账。" + "详询客服。" * 30
    assert len(fits) <= 400
    assert _submit(client, SUPPORT, template=fits).status_code == 202


def test_empty_template_is_rejected(client: TestClient) -> None:
    assert _submit(client, SUPPORT, template="   ").status_code == 422


def test_confirmation_gate_is_not_writable(client: TestClient) -> None:
    before = _refund_rule(client)
    assert _submit(client, SUPPORT, requires_confirmation=False).status_code == 422
    assert _refund_rule(client) == before


def test_cooldown_is_bounded(client: TestClient) -> None:
    before = _refund_rule(client)
    assert _submit(client, SUPPORT, cooldown_minutes=-1).status_code == 422
    assert _submit(client, SUPPORT, cooldown_minutes=1441).status_code == 422
    assert _refund_rule(client) == before

    approved = _apply(client, SUPPORT, APPROVER, cooldown_minutes=15)
    assert approved["applied_version"] == 2
    assert _refund_rule(client)["cooldown_minutes"] == 15


def test_change_reaches_the_next_notification_but_not_the_pending_one(
    client: TestClient,
) -> None:
    _opt_in(client)
    first = _emit(client, SUPPORT)
    assert first["status"] == "PENDING_CONFIRMATION"
    assert SEED_TAIL in first["notification"]["body"]
    assert first["notification"]["rule_version"] == 1

    # cooldown 0 so the second event key is not suppressed for the wrong reason
    _apply(client, SUPPORT, APPROVER, template=NEW_TEMPLATE, cooldown_minutes=0)

    listed = client.get("/api/v1/memory/notifications", headers=ALICE).json()
    pending = listed["notifications"][0]
    assert pending["notification_id"] == first["notification"]["notification_id"]
    assert pending["body"] == first["notification"]["body"]
    assert pending["rule_version"] == 1

    second = _emit(client, SUPPORT, event_key="ORD-T10-0002")
    assert (
        second["notification"]["body"]
        == "退款 129.00 元已到账，订单 ORD-T10-0001，有问题直接回复这条消息。"
    )
    assert second["notification"]["rule_version"] == 2
    assert SEED_TAIL not in second["notification"]["body"]


def test_disabling_a_rule_stops_emitting_and_re_enabling_restores_it(
    client: TestClient,
) -> None:
    _opt_in(client)
    _apply(client, SUPPORT, APPROVER, enabled=False)
    suppressed = _emit(client, SUPPORT)
    assert suppressed["status"] == "SUPPRESSED"
    assert suppressed["reason"] == "NO_RULE"

    _apply(client, SUPPORT, APPROVER, enabled=True)
    assert _emit(client, SUPPORT)["status"] == "PENDING_CONFIRMATION"


def test_zero_cooldown_lets_the_next_event_through_immediately(
    client: TestClient,
) -> None:
    _opt_in(client)
    assert _emit(client, SUPPORT)["status"] == "PENDING_CONFIRMATION"
    assert _emit(client, SUPPORT, event_key="ORD-T10-0002")["reason"] == "COOLDOWN"

    _apply(client, SUPPORT, APPROVER, cooldown_minutes=0)
    assert (
        _emit(client, SUPPORT, event_key="ORD-T10-0003")["status"]
        == "PENDING_CONFIRMATION"
    )


def test_audit_holds_digests_of_the_words_not_the_words(
    app: FastAPI, client: TestClient
) -> None:
    template = f"退款已到账，{MARKER}，订单 {{order_id}}。"
    reason = f"{MARKER}-REASON"
    submitted = _submit(client, SUPPORT, template=template, cooldown_minutes=5, reason=reason)
    change_id = submitted.json()["change_id"]
    assert _approve(client, change_id).status_code == 200

    submits = _audit_rows(app, "proactive.rule.submit")
    assert len(submits) == 1
    submit = submits[0]
    assert submit.resource_type == "proactive_rule"
    assert submit.resource_id == "rule_demo_refund_settled"
    assert submit.actor_id == "agent-1"
    assert submit.detail == {
        "event_type": REFUND,
        "change_id": change_id,
        "kind": "UPDATE",
        "base_version": 1,
        "fields": ["cooldown_minutes", "template"],
        "enabled": True,
        "cooldown_minutes": 5,
        "template_sha256": _digest(template),
        "reason_sha256": _digest(reason),
        "target_version": None,
    }

    approves = _audit_rows(app, "proactive.rule.approve")
    assert len(approves) == 1
    approve = approves[0]
    assert approve.resource_type == "proactive_rule"
    assert approve.resource_id == "rule_demo_refund_settled"
    assert approve.actor_id == "agent-9"
    assert approve.detail["change_id"] == change_id
    assert approve.detail["from_version"] == 1
    assert approve.detail["to_version"] == 2
    assert approve.detail["submitted_by"] == "agent-1"
    assert approve.detail["approved_by"] == "agent-9"
    assert approve.detail["template_sha256"] == _digest(template)
    assert approve.detail["previous_template_sha256"] == _digest(SEED_TEMPLATE)

    for row in (*submits, *approves):
        serialized = json.dumps(row.detail, ensure_ascii=False)
        assert MARKER not in serialized


def test_submitter_cannot_approve_their_own_change(client: TestClient) -> None:
    before = _refund_rule(client)
    submitted = _submit(client, SUPPORT, template=NEW_TEMPLATE)
    change_id = submitted.json()["change_id"]

    blocked = _approve(client, change_id, SUPPORT_SELF_APPROVER)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "RULE_SELF_APPROVAL"
    assert _refund_rule(client) == before
    assert _changes(client)[0]["status"] == "PENDING"


def test_approval_requires_the_approver_role(client: TestClient) -> None:
    before = _refund_rule(client)
    change_id = _submit(client, SUPPORT, template=NEW_TEMPLATE).json()["change_id"]

    blocked = _approve(client, change_id, SUPPORT_OTHER)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "FORBIDDEN"
    assert _refund_rule(client) == before


def test_a_second_pending_change_is_rejected(client: TestClient) -> None:
    first = _submit(client, SUPPORT, template=NEW_TEMPLATE)
    assert first.status_code == 202

    second = _submit(client, SUPPORT, template="另一版文案 {order_id}。")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "RULE_CHANGE_PENDING"
    assert len(_changes(client)) == 1
    assert _refund_rule(client)["template"] == SEED_TEMPLATE


def test_reject_leaves_the_rule_and_versions_untouched(
    app: FastAPI, client: TestClient
) -> None:
    before = _refund_rule(client)
    change_id = _submit(client, SUPPORT, template=NEW_TEMPLATE).json()["change_id"]

    rejected = _reject(client, change_id, APPROVER, note="口径再确认")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"
    assert rejected.json()["decided_by"] == "agent-9"
    assert rejected.json()["decision_note"] == "口径再确认"
    assert _refund_rule(client) == before
    assert [version["version"] for version in _versions(client)] == [1]

    rows = _audit_rows(app, "proactive.rule.reject")
    assert len(rows) == 1
    assert rows[0].detail["change_id"] == change_id
    assert rows[0].detail["from_version"] == 1
    assert rows[0].detail["reason_sha256"] == _digest("口径再确认")


def test_a_stale_base_version_cannot_be_approved(app: FastAPI, client: TestClient) -> None:
    change_id = _submit(client, SUPPORT, template=NEW_TEMPLATE).json()["change_id"]

    # Simulate the concurrent writer that bumped the rule between submit and
    # approve. The approval must notice and refuse rather than overwrite it.
    with app.state.session_factory() as session:
        row = session.scalars(
            select(ProactiveRule).where(
                ProactiveRule.tenant_id == "demo",
                ProactiveRule.event_type == REFUND,
            )
        ).one()
        row.version = 2
        row.template = "并发写入的文案 {order_id}。"
        session.commit()

    blocked = _approve(client, change_id)
    assert blocked.status_code == 409
    error = blocked.json()["error"]
    assert error["code"] == "RULE_VERSION_CONFLICT"
    assert error["details"] == {"base_version": 1, "current_version": 2}
    assert _refund_rule(client)["template"] == "并发写入的文案 {order_id}。"


def test_version_history_is_immutable_and_rollback_is_a_new_version(
    client: TestClient,
) -> None:
    baseline = _versions(client)
    assert [(item["version"], item["template"]) for item in baseline] == [
        (1, SEED_TEMPLATE)
    ]
    assert baseline[0]["change_id"] == "seed"
    assert baseline[0]["approved_by"] == "system"

    _apply(client, SUPPORT, APPROVER, template=NEW_TEMPLATE, cooldown_minutes=0)
    _apply(client, SUPPORT, APPROVER, template="第二版 {order_id} 已处理。", reason="再改")
    history = _versions(client)
    assert [(item["version"], item["template"]) for item in history] == [
        (3, "第二版 {order_id} 已处理。"),
        (2, NEW_TEMPLATE),
        (1, SEED_TEMPLATE),
    ]
    assert history[0]["submitted_by"] == "agent-1"
    assert history[0]["approved_by"] == "agent-9"
    assert history[0]["reason"] == "再改"

    submitted = _rollback(client, 1, reason="回到最初")
    assert submitted.status_code == 202
    proposal = submitted.json()
    assert proposal["kind"] == "ROLLBACK"
    assert proposal["target_version"] == 1
    assert proposal["template"] == SEED_TEMPLATE
    assert proposal["base_version"] == 3
    # A rollback is still a proposal: the live rule has not moved yet.
    assert _refund_rule(client)["template"] == "第二版 {order_id} 已处理。"

    approved = _approve(client, proposal["change_id"])
    assert approved.status_code == 200
    assert approved.json()["applied_version"] == 4
    assert _refund_rule(client)["template"] == SEED_TEMPLATE
    assert [item["version"] for item in _versions(client)] == [4, 3, 2, 1]
    assert _versions(client)[0]["template"] == SEED_TEMPLATE
    assert _versions(client)[1]["template"] == "第二版 {order_id} 已处理。"


def test_rollback_to_the_current_version_is_refused(client: TestClient) -> None:
    response = _rollback(client, 1)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RULE_ALREADY_AT_VERSION"


def test_notification_keeps_the_version_that_wrote_it(client: TestClient) -> None:
    _opt_in(client)
    _apply(client, SUPPORT, APPROVER, template=NEW_TEMPLATE, cooldown_minutes=0)
    first = _emit(client, SUPPORT)
    assert first["notification"]["rule_version"] == 2
    assert first["notification"]["body"].startswith("退款 129.00 元已到账")

    change_id = _rollback(client, 1).json()["change_id"]
    assert _approve(client, change_id).status_code == 200

    listed = client.get("/api/v1/memory/notifications", headers=ALICE).json()
    assert listed["notifications"][0]["body"] == first["notification"]["body"]
    assert listed["notifications"][0]["rule_version"] == 2

    _opt_in(client, BOB)
    second = _emit(client, SUPPORT, user_id="bob", event_key="ORD-T10-9001")
    assert second["notification"]["rule_version"] == 3
    assert SEED_TAIL in second["notification"]["body"]


def test_real_http_walkthrough_submit_approve_effective_rollback(
    client: TestClient,
) -> None:
    _opt_in(client)
    before = _emit(client, SUPPORT)["notification"]["body"]
    assert SEED_TAIL in before

    submitted = _submit(
        client, SUPPORT, template=NEW_TEMPLATE, cooldown_minutes=0, reason="演示"
    )
    change_id = submitted.json()["change_id"]
    assert _rules(client)[1]["template"] == SEED_TEMPLATE

    assert _approve(client, change_id).status_code == 200
    after = _emit(client, SUPPORT, event_key="ORD-T10-0002")["notification"]
    assert after["body"] == NEW_TEMPLATE.format(
        amount="129.00", order_id="ORD-T10-0001"
    )
    assert after["rule_version"] == 2

    rollback_id = _rollback(client, 1).json()["change_id"]
    assert _approve(client, rollback_id).status_code == 200
    assert _refund_rule(client)["template"] == SEED_TEMPLATE

    _opt_in(client, BOB)
    restored = _emit(client, SUPPORT, user_id="bob", event_key="ORD-T10-0003")[
        "notification"
    ]
    assert restored["body"] == SEED_TEMPLATE.format(
        order_id="ORD-T10-0001", amount="129.00"
    )
    assert restored["rule_version"] == 3


def test_resolved_changes_cannot_be_decided_again(client: TestClient) -> None:
    change_id = _submit(client, SUPPORT, template=NEW_TEMPLATE).json()["change_id"]
    assert _approve(client, change_id).status_code == 200

    again = _approve(client, change_id)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "RULE_CHANGE_NOT_PENDING"

    rejected = _reject(client, change_id)
    assert rejected.status_code == 409


def test_a_tenant_the_seed_never_covered_can_turn_it_on(client: TestClient) -> None:
    assert _rules(client, SUPPORT_ACME) == []
    assert _versions(client, SUPPORT_ACME) == []

    submitted = _submit(
        client,
        SUPPORT_ACME,
        template="您的订单 {order_id} 退款 {amount} 元已到账。",
        cooldown_minutes=30,
    )
    assert submitted.status_code == 202
    assert submitted.json()["base_version"] == 0
    assert _rules(client, SUPPORT_ACME) == []

    approved = _approve(client, submitted.json()["change_id"], APPROVER_ACME)
    assert approved.status_code == 200
    created = _rules(client, SUPPORT_ACME)[0]
    assert created["rule_id"] == "rule_acme_refund_settled"
    assert created["version"] == 1
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
    change = _apply(
        client, SUPPORT_ACME, APPROVER_ACME, enabled=False, template="订单 {order_id} 有进展。"
    )
    assert change["enabled"] is False
    assert change["cooldown_minutes"] == 60
    assert change["requires_confirmation"] is True
    rule = _rules(client, SUPPORT_ACME)[0]
    assert rule["enabled"] is False
    assert rule["version"] == 1


def test_new_rule_needs_a_template(client: TestClient) -> None:
    assert _rules(client, SUPPORT_ACME) == []
    response = _submit(client, SUPPORT_ACME, cooldown_minutes=30)
    assert response.status_code == 422
    assert "template" in response.json()["error"]["message"]
    assert _rules(client, SUPPORT_ACME) == []
    assert _changes(client, SUPPORT_ACME) == []


def test_rejected_create_persists_nothing(app: FastAPI, client: TestClient) -> None:
    assert (
        _submit(client, SUPPORT_ACME, template="订单 {invoice_no} 已开票。").status_code
        == 422
    )
    assert _rules(client, SUPPORT_ACME) == []
    assert _changes(client, SUPPORT_ACME) == []
    assert _rule_rows(app, "acme") == []


def test_rules_and_changes_do_not_cross_tenants(client: TestClient) -> None:
    demo_before = _rules(client)
    acme_change = _apply(
        client, SUPPORT_ACME, APPROVER_ACME, template=NEW_TEMPLATE
    )

    assert [rule["event_type"] for rule in _rules(client, SUPPORT_ACME)] == [REFUND]
    assert _rules(client) == demo_before

    # The Acme change id is not a handle the demo tenant can read or decide.
    assert (
        client.get(
            f"/api/v1/memory/rules/changes/{acme_change['change_id']}",
            headers=SUPPORT,
        ).status_code
        == 404
    )
    assert _approve(client, acme_change["change_id"], APPROVER).status_code == 404
    assert _reject(client, acme_change["change_id"], APPROVER).status_code == 404
    demo_versions = _versions(client, SUPPORT)
    assert [(item["version"], item["template"]) for item in demo_versions] == [
        (1, SEED_TEMPLATE)
    ]
    assert demo_versions[0]["change_id"] == "seed"
    assert demo_versions[0]["approved_by"] == "system"

    # Lists are tenant-filtered, not 404: each tenant sees only its own rows.
    acme_versions = _versions(client, SUPPORT_ACME)
    assert [item["version"] for item in acme_versions] == [1]
    assert acme_versions[0]["change_id"] == acme_change["change_id"]
    assert all(
        item["change_id"] != acme_change["change_id"] for item in demo_versions
    )
    acme_changes = _changes(client, SUPPORT_ACME)
    assert [item["change_id"] for item in acme_changes] == [acme_change["change_id"]]
    assert _changes(client) == []


def test_cross_tenant_cannot_roll_back_another_tenants_version(
    client: TestClient,
) -> None:
    # Acme is a seat in its own tenant but has no rule to roll back; a demo
    # version number is not a handle it can spend.
    response = _rollback(client, 1, SUPPORT_ACME, event_type=REFUND)
    assert response.status_code == 404
    assert _rules(client, SUPPORT_ACME) == []


def test_empty_change_is_rejected(client: TestClient) -> None:
    assert _submit(client, SUPPORT).status_code == 422


def test_changes_are_listed_with_the_decision_metadata(client: TestClient) -> None:
    first = _submit(client, SUPPORT, template=NEW_TEMPLATE, reason="第一条").json()
    pending = _changes(client)
    assert len(pending) == 1
    assert pending[0]["change_id"] == first["change_id"]
    assert pending[0]["status"] == "PENDING"
    assert pending[0]["submitted_by"] == "agent-1"
    assert pending[0]["reason"] == "第一条"
    assert pending[0]["old_template"] == SEED_TEMPLATE
    assert pending[0]["old_cooldown_minutes"] == 60

    rejected = _reject(client, first["change_id"], APPROVER, note="先不做").json()
    assert rejected["status"] == "REJECTED"
    assert rejected["decision_note"] == "先不做"
    assert _changes(client, status="PENDING") == []
    resolved = _changes(client, status="REJECTED")
    assert [item["change_id"] for item in resolved] == [first["change_id"]]


def test_reviewer_fixture_can_approve_a_seat_change(client: TestClient) -> None:
    # The console identity preset uses the same split: seat submits, reviewer
    # (approver + support) decides, and the role gate is the real permission.
    change_id = _submit(client, SUPPORT, template=NEW_TEMPLATE).json()["change_id"]
    approved = _approve(client, change_id, REVIEWER)
    assert approved.status_code == 200
    assert approved.json()["decided_by"] == "reviewer"
    assert approved.json()["applied_version"] == 2
