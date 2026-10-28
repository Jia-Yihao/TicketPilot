"""Offline evaluation for the read path and multi-turn conversations.

The golden set exercises the write path one task at a time. This runner does
the same for the support agent: it drives real conversations through the HTTP
API, follows a clarification to the resumed turn, and checks what the trace
says the agent understood.

Three kinds of assertions matter here:

* the reply the customer reads;
* the clarification the agent asked for (reason, slot, resumable intent);
* the resumed turn — the same intent comes back and the missing slot is
  supplied, so the request actually reaches the runtime.

Task operations (cancel, approve, expire) are applied between turns through
the public task API, so a conversation that produced a task can be followed
to its final state.

A case may also carry a profile precondition (``setup_profile``) written
through the public memory API before its first turn, and a dataset may ask for
every case to start from no profile at all (``reset_profile``). Those two,
plus the per-turn ``new_conversation`` flag, are how the memory and multi-turn
set pins "what is remembered across sessions" and "what is not".
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent.intent_catalog import get_catalog
from app.core.config import Settings
from app.core.time import utcnow
from app.main import create_app
from app.models.audit import AuditEvent
from app.models.conversation import Conversation, ConversationMessage
from app.seed import seed_all

try:
    from evals.fixtures import apply_fixtures
    from evals.run import (
        _expire_pending_approvals,
        _fill_summary,
        _headers,
        _leftover_state,
        _reset_state,
        _write_summary,
    )
except ModuleNotFoundError:  # `python evals/support_run.py` puts evals/ on sys.path
    from fixtures import apply_fixtures
    from run import (
        _expire_pending_approvals,
        _fill_summary,
        _headers,
        _leftover_state,
        _reset_state,
        _write_summary,
    )

MIN_SUPPORT_CASES = 60
_KINDS = frozenset({"normal", "boundary", "failure"})

# Every key a turn's ``expect`` block may use. An unknown key is a typo, and a
# typo used to be the quietest kind of failure: the assertion you meant to make
# simply never ran, and the case passed while proving nothing. So the set is
# closed and a key outside it is reported like any other failure.
_EXPECT_KEYS = frozenset(
    {
        # assertions the support set already used
        "agent_silent",
        "clarification",
        "handoff",
        "http_status",
        "inherited_slots",
        "intent",
        "reply_contains",
        "reply_not_contains",
        "slot_contains",
        "slot_not_contains",
        "slots",
        "span_absent",
        "span_attrs",
        "spans",
        "task",
        # assertions the memory / multi-turn set needs
        "card_ids",
        "inherited_slots_empty",
        "memory_audit_unchanged",
        "personalization",
        "stored_messages",
        "stored_messages_max",
        "suggestion_count",
        "suggestion_count_min",
        "suggestions_not_contains",
    }
)


# ------------------------------------------------------------------ helpers
def _span_key(span: dict[str, Any]) -> str:
    return f"{span['kind']}/{span['name']}"


def _span_by_name(spans: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((span for span in spans if span["name"] == name), None)


def _fetch_trace(
    client: TestClient, trace_id: str | None, headers: dict[str, str]
) -> dict[str, Any] | None:
    if not trace_id:
        return None
    response = client.get(f"/api/v1/traces/{trace_id}", headers=headers)
    if response.status_code != 200:
        return None
    return response.json()


def _clarification_failures(label: str, expect: Any, body: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    actual = body.get("clarification")
    if expect is None:
        if actual is not None:
            failures.append(f"{label}.clarification: expected none, got {actual.get('reason')!r}")
        # A handoff may leave the unfinished request in the slots for the
        # human who takes over, so only a served turn must clear it.
        slots = body.get("slots") or {}
        if body.get("handoff") is None and slots.get("pending_intent") is not None:
            failures.append(
                f"{label}.slots.pending_intent: expected none, got {slots.get('pending_intent')!r}"
            )
        return failures
    if actual is None:
        failures.append(f"{label}.clarification: expected a clarification, got none")
        return failures
    for key in ("reason", "expected_slot", "resumable_intent"):
        if key in expect and actual.get(key) != expect[key]:
            failures.append(
                f"{label}.clarification.{key}: expected {expect[key]!r}, got {actual.get(key)!r}"
            )
    return failures


def _task_failures(label: str, expect: Any, body: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    task = body.get("task")
    if expect is None:
        if task is not None:
            failures.append(f"{label}.task: expected none, got {task.get('status')!r}")
        return failures
    if task is None:
        failures.append(f"{label}.task: expected a task, got none")
        return failures
    result = task.get("result") or {}
    if "status" in expect and task.get("status") != expect["status"]:
        failures.append(
            f"{label}.task.status: expected {expect['status']!r}, got {task.get('status')!r}"
        )
    if "outcome" in expect and result.get("outcome") != expect["outcome"]:
        failures.append(
            f"{label}.task.result.outcome: expected {expect['outcome']!r}, "
            f"got {result.get('outcome')!r}"
        )
    if "error_code" in expect and result.get("error_code") != expect["error_code"]:
        failures.append(
            f"{label}.task.result.error_code: expected {expect['error_code']!r}, "
            f"got {result.get('error_code')!r}"
        )
    if "pending_amount_cents" in expect:
        actual = (task.get("pending_approval") or {}).get("amount_cents")
        if actual != expect["pending_amount_cents"]:
            failures.append(
                f"{label}.task.pending_approval.amount_cents: expected "
                f"{expect['pending_amount_cents']!r}, got {actual!r}"
            )
    for needle in expect.get("contains", []):
        if needle not in json.dumps(task, ensure_ascii=False):
            failures.append(f"{label}.task.contains: missing {needle!r}")
    return failures


def _trace_failures(
    label: str,
    expect: dict[str, Any],
    trace_body: dict[str, Any] | None,
) -> list[str]:
    failures: list[str] = []
    if trace_body is None:
        return [f"{label}.trace: not found"]
    spans = trace_body.get("spans") or []
    keys = {_span_key(span) for span in spans}
    for key in expect.get("spans", []):
        if key not in keys:
            failures.append(f"{label}.trace.spans: missing {key!r}")
    for key in expect.get("span_absent", []):
        if key in keys:
            failures.append(f"{label}.trace.spans: unexpected {key!r}")
    for name, attrs in (expect.get("span_attrs") or {}).items():
        span = _span_by_name(spans, name)
        if span is None:
            failures.append(f"{label}.trace.{name}: span not found")
            continue
        actual = span.get("attributes") or {}
        for key, value in attrs.items():
            if actual.get(key) != value:
                failures.append(
                    f"{label}.trace.{name}.attrs.{key}: expected {value!r}, got {actual.get(key)!r}"
                )
    return failures


def _personalization_failures(
    label: str, expect: Any, body: dict[str, Any]
) -> list[str]:
    """``null`` means the answer was not reshaped at all; a dict names effects.

    A missing body field reads as the empty result, so a case that only needs
    "this effect did not run" does not have to spell out ``null``.
    """
    actual = body.get("personalization")
    if expect is None:
        if actual is not None:
            return [
                f"{label}.personalization: expected none, got {actual.get('effects')!r}"
            ]
        return []
    effects = list((actual or {}).get("effects") or [])
    fields = list((actual or {}).get("fields") or [])
    failures: list[str] = []
    for effect in expect.get("effects_contains", []):
        if effect not in effects:
            failures.append(
                f"{label}.personalization.effects: missing {effect!r}, got {effects}"
            )
    for effect in expect.get("effects_absent", []):
        if effect in effects:
            failures.append(f"{label}.personalization.effects: unexpected {effect!r}")
    if "fields" in expect and fields != list(expect["fields"]):
        failures.append(
            f"{label}.personalization.fields: expected {list(expect['fields'])!r}, "
            f"got {fields!r}"
        )
    return failures


def _stored_message_count(app: FastAPI, conversation_id: str | None) -> int:
    """How many transcript rows this conversation still holds after the turn."""
    if not conversation_id:
        return 0
    with app.state.session_factory() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id)
            )
            or 0
        )


def _memory_audit_count(app: FastAPI, actor: dict[str, Any]) -> int:
    """``memory.%`` audit rows this caller already has.

    The answer path must add none: reading the profile is not an event, and a
    customer denying their memory must not create one either.
    """
    with app.state.session_factory() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.action.like("memory.%"),
                    AuditEvent.tenant_id == str(actor["tenant"]),
                    AuditEvent.actor_id == str(actor["user"]),
                )
            )
            or 0
        )


def _turn_failures(
    turn: dict[str, Any],
    index: int,
    response: Any,
    body: dict[str, Any],
    trace_body: dict[str, Any] | None,
) -> list[str]:
    expect = turn.get("expect") or {}
    label = f"turns[{index}]"
    failures: list[str] = []

    for key in expect:
        if key not in _EXPECT_KEYS:
            failures.append(f"{label}.expect: unknown key {key!r}")

    if response.status_code != expect.get("http_status", 200):
        failures.append(
            f"{label}.http_status: expected {expect.get('http_status', 200)}, "
            f"got {response.status_code}"
        )

    intent = expect.get("intent")
    if intent is not None and body.get("intent") != intent:
        failures.append(f"{label}.intent: expected {intent!r}, got {body.get('intent')!r}")

    if "agent_silent" in expect and body.get("agent_silent") != expect["agent_silent"]:
        failures.append(
            f"{label}.agent_silent: expected {expect['agent_silent']!r}, "
            f"got {body.get('agent_silent')!r}"
        )

    reply = str(body.get("reply") or "")
    for needle in expect.get("reply_contains", []):
        if needle not in reply:
            failures.append(f"{label}.reply: missing {needle!r}")
    for needle in expect.get("reply_not_contains", []):
        if needle in reply:
            failures.append(f"{label}.reply: leaked {needle!r}")

    if "clarification" in expect:
        failures.extend(_clarification_failures(label, expect["clarification"], body))

    if "task" in expect:
        failures.extend(_task_failures(label, expect["task"], body))

    if "handoff" in expect:
        actual = body.get("handoff")
        if expect["handoff"] is None:
            if actual is not None:
                failures.append(f"{label}.handoff: expected none, got {actual.get('status')!r}")
        else:
            if actual is None:
                failures.append(f"{label}.handoff: expected a handoff, got none")
            else:
                for key, value in expect["handoff"].items():
                    if actual.get(key) != value:
                        failures.append(
                            f"{label}.handoff.{key}: expected {value!r}, got {actual.get(key)!r}"
                        )

    slots = body.get("slots") or {}
    for key, value in (expect.get("slots") or {}).items():
        if slots.get(key) != value:
            failures.append(f"{label}.slots.{key}: expected {value!r}, got {slots.get(key)!r}")
    for key, needle in (expect.get("slot_contains") or {}).items():
        if needle not in str(slots.get(key) or ""):
            failures.append(f"{label}.slots.{key}: missing {needle!r}")
    for key, needle in (expect.get("slot_not_contains") or {}).items():
        if needle in str(slots.get(key) or ""):
            failures.append(f"{label}.slots.{key}: leaked {needle!r}")

    inherited = body.get("inherited_slots") or []
    for slot in expect.get("inherited_slots", []):
        if slot not in inherited:
            failures.append(f"{label}.inherited_slots: missing {slot!r}, got {inherited}")
    if expect.get("inherited_slots_empty") and inherited:
        failures.append(f"{label}.inherited_slots: expected none, got {inherited}")

    if "personalization" in expect:
        failures.extend(_personalization_failures(label, expect["personalization"], body))

    suggestions = [str(item) for item in (body.get("suggestions") or [])]
    if "suggestion_count" in expect and len(suggestions) != expect["suggestion_count"]:
        failures.append(
            f"{label}.suggestions: expected {expect['suggestion_count']} item(s), "
            f"got {len(suggestions)}"
        )
    if (
        "suggestion_count_min" in expect
        and len(suggestions) < expect["suggestion_count_min"]
    ):
        failures.append(
            f"{label}.suggestions: expected at least {expect['suggestion_count_min']} "
            f"item(s), got {len(suggestions)}"
        )
    for needle in expect.get("suggestions_not_contains", []):
        if any(needle in item for item in suggestions):
            failures.append(f"{label}.suggestions: leaked {needle!r}")

    if "card_ids" in expect:
        card_ids = [card.get("card_id") for card in body.get("context_cards") or []]
        if card_ids != list(expect["card_ids"]):
            failures.append(
                f"{label}.context_cards: expected {list(expect['card_ids'])!r}, "
                f"got {card_ids!r}"
            )

    failures.extend(_trace_failures(label, expect, trace_body))
    return failures


# ------------------------------------------------------------- task steps
def _apply_task_op(
    app: FastAPI,
    client: TestClient,
    conversation_id: str,
    task_id: str | None,
    op: dict[str, Any],
) -> list[str]:
    action = op["action"]
    expect = op.get("expect") or {}
    headers = _headers(op.get("actor") or {"tenant": "demo", "user": "alice"})

    if action == "rewind_idle":
        minutes = int(op.get("minutes", 60))
        with app.state.session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                return [f"op.{action}: conversation not found"]
            slots = dict(conversation.slots or {})
            slots["last_message_at"] = (utcnow() - timedelta(minutes=minutes)).isoformat()
            conversation.slots = slots
            session.commit()
        return []

    if task_id is None:
        return [f"op.{action}: no task was created before this step"]

    if action == "expire_approval":
        expired = _expire_pending_approvals(app, task_id)
        minimum = int(expect.get("expired_min", 1))
        if expired < minimum:
            return [f"op.{action}.expired: expected >= {minimum}, got {expired}"]
        return []

    if action == "approve":
        response = client.post(
            f"/api/v1/tasks/{task_id}/approve",
            headers=headers,
            json={
                "decision": op.get("decision", "APPROVE"),
                "reason": op.get("reason"),
                "approval_id": op.get("approval_id"),
            },
        )
    elif action == "reject":
        response = client.post(
            f"/api/v1/tasks/{task_id}/approve",
            headers=headers,
            json={
                "decision": op.get("decision", "REJECT"),
                "reason": op.get("reason"),
                "approval_id": op.get("approval_id"),
            },
        )
    elif action == "cancel":
        response = client.post(f"/api/v1/tasks/{task_id}/cancel", headers=headers)
    elif action == "get_task":
        response = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    else:
        return [f"op.{action}: unsupported action"]

    failures: list[str] = []
    body = response.json()
    if "http_status" in expect and response.status_code != expect["http_status"]:
        failures.append(
            f"op.{action}.http_status: expected {expect['http_status']}, got {response.status_code}"
        )
    text = json.dumps(body, ensure_ascii=False)
    for needle in expect.get("contains", []):
        if needle not in text:
            failures.append(f"op.{action}.contains: missing {needle!r}")
    if "task_status" in expect and body.get("status") != expect["task_status"]:
        failures.append(
            f"op.{action}.task_status: expected {expect['task_status']!r}, "
            f"got {body.get('status')!r}"
        )
    result = body.get("result") or {}
    if "outcome" in expect and result.get("outcome") != expect["outcome"]:
        failures.append(
            f"op.{action}.outcome: expected {expect['outcome']!r}, got {result.get('outcome')!r}"
        )
    if "error_code" in expect:
        actual = (body.get("error") or {}).get("code") or result.get("error_code")
        if actual != expect["error_code"]:
            failures.append(
                f"op.{action}.error_code: expected {expect['error_code']!r}, got {actual!r}"
            )
    if "latest_approval_status" in expect:
        actual = (body.get("latest_approval") or {}).get("status")
        if actual != expect["latest_approval_status"]:
            failures.append(
                f"op.{action}.latest_approval.status: expected "
                f"{expect['latest_approval_status']!r}, got {actual!r}"
            )
    if "pending_amount_cents" in expect:
        actual = (body.get("pending_approval") or {}).get("amount_cents")
        if actual != expect["pending_amount_cents"]:
            failures.append(
                f"op.{action}.pending_approval.amount_cents: expected "
                f"{expect['pending_amount_cents']!r}, got {actual!r}"
            )
    return failures


def _run_conversation(
    app: FastAPI,
    client: TestClient,
    case: dict[str, Any],
    *,
    reset_profile: bool = False,
) -> list[str]:
    failures: list[str] = []
    actor = {"tenant": case["tenant"], "user": case["user"]}
    headers = _headers(actor)
    conversation_id: str | None = None
    turn_tasks: list[str | None] = []
    ops = list(case.get("task_ops") or [])
    profile_ops = list(case.get("profile_ops") or [])
    turns = case["turns"]

    if reset_profile:
        # Every case starts from "this customer has no profile": one case's
        # setup must never become the next case's precondition.
        status = client.delete("/api/v1/memory", headers=headers).status_code
        if status != 204:
            failures.append(f"reset_profile.http_status: expected 204, got {status}")

    if case.get("setup_profile") is not None:
        status = client.put(
            "/api/v1/memory", headers=headers, json=case["setup_profile"]
        ).status_code
        if status != 200:
            failures.append(f"setup_profile.http_status: expected 200, got {status}")

    for index, turn in enumerate(turns):
        for op_index, op in enumerate(ops):
            if op.get("before_turn") == index:
                failures.extend(
                    f"task_ops[{op_index}].{item}"
                    for item in _apply_task_op(
                        app,
                        client,
                        conversation_id or "",
                        turn_tasks[-1] if turn_tasks else None,
                        op,
                    )
                )

        if turn.get("new_conversation"):
            # The profile travels with the customer; the conversation slots do
            # not. Dropping the id is the whole mechanism.
            conversation_id = None

        expect = turn.get("expect") or {}
        audit_before = (
            _memory_audit_count(app, actor)
            if expect.get("memory_audit_unchanged")
            else None
        )

        payload: dict[str, Any] = {"message": turn["message"]}
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        if turn.get("order_id"):
            payload["order_id"] = turn["order_id"]
        response = client.post("/api/v1/support/messages", headers=headers, json=payload)
        body = response.json()
        conversation_id = body.get("conversation_id") or conversation_id
        task = body.get("task") or {}
        turn_tasks.append(task.get("task_id"))
        trace_body = _fetch_trace(client, body.get("trace_id"), headers)
        failures.extend(_turn_failures(turn, index, response, body, trace_body))

        if "stored_messages" in expect or "stored_messages_max" in expect:
            stored = _stored_message_count(app, conversation_id)
            if "stored_messages" in expect and stored != expect["stored_messages"]:
                failures.append(
                    f"turns[{index}].stored_messages: expected "
                    f"{expect['stored_messages']}, got {stored}"
                )
            if "stored_messages_max" in expect and stored > expect["stored_messages_max"]:
                failures.append(
                    f"turns[{index}].stored_messages: expected at most "
                    f"{expect['stored_messages_max']}, got {stored}"
                )

        if audit_before is not None:
            audit_after = _memory_audit_count(app, actor)
            if audit_after != audit_before:
                failures.append(
                    f"turns[{index}].memory_audit: expected no new memory.% rows, "
                    f"got {audit_before} -> {audit_after}"
                )

        for op_index, op in enumerate(ops):
            if op.get("after_turn") == index:
                failures.extend(
                    f"task_ops[{op_index}].{item}"
                    for item in _apply_task_op(
                        app, client, conversation_id or "", task.get("task_id"), op
                    )
                )

        for op_index, op in enumerate(profile_ops):
            if op.get("after_turn") != index:
                continue
            action = op.get("action")
            if action != "delete":
                failures.append(
                    f"profile_ops[{op_index}]: unsupported action {action!r}"
                )
                continue
            status = client.delete("/api/v1/memory", headers=headers).status_code
            if status != 204:
                failures.append(
                    f"profile_ops[{op_index}].http_status: expected 204, got {status}"
                )
    return failures


# --------------------------------------------------------------- dataset IO
def _dataset_tenants(dataset: dict[str, Any]) -> set[str]:
    return {case["tenant"] for case in dataset["cases"] if case.get("tenant")}


def _validate_dataset(dataset: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    cases = dataset.get("cases", [])
    known = set(get_catalog().specs)
    seen: set[str] = set()
    covered: set[str] = set()
    for index, case in enumerate(cases):
        label = case.get("id", f"case[{index}]")
        if label in seen:
            failures.append(f"{label}: duplicate case id")
        seen.add(label)
        for key in ("id", "query", "intent", "kind", "tenant", "user", "turns"):
            if key not in case:
                failures.append(f"{label}: missing key {key!r}")
        if case.get("kind") not in _KINDS:
            failures.append(f"{label}: unknown kind {case.get('kind')!r}")
        turns = case.get("turns") or []
        if not turns:
            failures.append(f"{label}: no turns")
            continue
        if turns[0].get("message") != case.get("query"):
            failures.append(f"{label}: turns[0] must repeat query")
        for turn_index, turn in enumerate(turns):
            expect = turn.get("expect") or {}
            intent = expect.get("intent")
            if intent is None:
                failures.append(f"{label}.turns[{turn_index}]: missing intent")
            elif intent not in known:
                failures.append(f"{label}.turns[{turn_index}]: unknown intent {intent!r}")
            else:
                covered.add(intent)
    if len(cases) < MIN_SUPPORT_CASES:
        failures.append(f"dataset: expected at least {MIN_SUPPORT_CASES} cases, found {len(cases)}")
    missing = sorted(known - covered)
    if missing:
        failures.append(f"dataset: intents not covered: {missing}")
    return failures


def _validate_classes(dataset: dict[str, Any]) -> list[str]:
    """The dataset-class contract, checked in partial mode too.

    A set that only proves "the thing we wanted to remember was remembered"
    quietly rewards an agent that injects memory everywhere. So every required
    class must carry both a positive and a negative case, and a class nobody
    wrote a case for is reported rather than silently skipped.
    """
    required = dataset.get("required_classes")
    if not required:
        return []
    failures: list[str] = []
    seen: dict[str, set[str]] = {str(name): set() for name in required}
    for index, case in enumerate(dataset.get("cases") or []):
        label = case.get("id", f"case[{index}]")
        name = case.get("class")
        polarity = case.get("polarity")
        if not isinstance(name, str) or not name:
            failures.append(f"{label}: missing class")
            continue
        if name not in seen:
            failures.append(f"{label}: class {name!r} is not in required_classes")
        if polarity not in ("positive", "negative"):
            failures.append(f"{label}: unknown polarity {polarity!r}")
            continue
        if name in seen:
            seen[name].add(str(polarity))
    for name, polarities in seen.items():
        if not polarities:
            failures.append(f"class {name}: no cases")
            continue
        if "positive" not in polarities:
            failures.append(f"class {name}: missing positive case")
        if "negative" not in polarities:
            failures.append(f"class {name}: missing negative case")
    return failures


def _run_dataset(
    dataset: dict[str, Any],
    database_url: str,
    reset: bool = False,
    partial: bool = False,
    summary: dict[str, Any] | None = None,
) -> int:
    if partial:
        # A targeted regression set (one behaviour, a handful of cases) must
        # not be forced to cover all 12 intents; it is run separately and never
        # replaces the full 62-case gate.
        if not dataset.get("cases"):
            print("DATASET partial set has no cases")
            _fill_summary(summary, 0, 0, [])
            return 2
    else:
        dataset_failures = _validate_dataset(dataset)
        if dataset_failures:
            for item in dataset_failures:
                print(f"DATASET {item}")
            _fill_summary(summary, 0, len(dataset.get("cases") or []), [])
            return 2

    class_failures = _validate_classes(dataset)
    if class_failures:
        for item in class_failures:
            print(f"DATASET {item}")
        _fill_summary(summary, 0, len(dataset.get("cases") or []), [])
        return 2

    settings = Settings(
        environment="test",
        database_url=database_url,
        auto_create_tables=True,
    )
    app = create_app(settings)
    tenants = _dataset_tenants(dataset)
    if reset:
        removed = _reset_state(app.state.session_factory, tenants)
        print(f"reset demo state for tenants {sorted(tenants)}: {removed}")
    else:
        leftover = _leftover_state(app.state.session_factory, tenants)
        if any(leftover.values()):
            app.state.engine.dispose()
            print(
                "目标数据库已有事务数据，Support Set 需要干净库："
                f"{ {k: v for k, v in leftover.items() if v} }。"
                " 加 --reset 清空演示租户的事务数据，或换一个空库。",
            )
            _fill_summary(summary, 0, len(dataset.get("cases") or []), [])
            return 2
    seed_all(app.state.session_factory)
    apply_fixtures(app.state.session_factory)
    client = TestClient(app)

    passed = 0
    failures: list[tuple[str, list[str]]] = []
    reset_profile = bool(dataset.get("reset_profile"))
    for case in dataset["cases"]:
        try:
            case_failures = _run_conversation(
                app, client, case, reset_profile=reset_profile
            )
        except Exception as exc:  # one broken case must not hide the rest
            case_failures = [f"exception: {type(exc).__name__}: {exc}"]
        if case_failures:
            failures.append((case["id"], case_failures))
            print(f"FAIL {case['id']}")
            for item in case_failures:
                print(f"     - {item}")
        else:
            passed += 1
            print(f"PASS {case['id']}")

    total = len(dataset["cases"])
    print(f"\n{passed}/{total} support cases passed")
    app.state.engine.dispose()
    _fill_summary(summary, passed, total, failures)
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline TicketPilot support conversation set."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).with_name("support_set.json"),
    )
    parser.add_argument(
        "--database-url",
        help="Run against this database instead of a temporary SQLite file.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear the dataset tenants' transactional rows before running.",
    )
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Run a targeted behaviour set without the 60-case / 12-intent shape checks.",
    )
    parser.add_argument(
        "--json",
        nargs="?",
        const="-",
        metavar="PATH",
        help="Write the pass/fail summary as JSON to PATH; with no PATH, print it to stdout.",
    )
    args = parser.parse_args()

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    summary: dict[str, Any] = {}
    if args.database_url:
        code = _run_dataset(
            dataset,
            args.database_url,
            reset=args.reset,
            partial=args.partial,
            summary=summary,
        )
    else:
        with tempfile.TemporaryDirectory() as directory:
            url = f"sqlite+pysqlite:///{Path(directory) / 'support.db'}"
            code = _run_dataset(dataset, url, partial=args.partial, summary=summary)
    if args.json:
        _write_summary(args.json, summary)
    return code


if __name__ == "__main__":
    sys.exit(main())
