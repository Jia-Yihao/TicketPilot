"""Replay persisted traces and diff the rerun against what was recorded.

A trace is the only artifact that explains why the agent answered the way it
did. Replay reads a conversation's recorded turns from the trace store, sends
the same customer messages to a freshly seeded instance, and compares two
things turn by turn:

* **conclusion** — what the customer got: intent, clarification, outcome,
  action and a digest of the reply;
* **trajectory** — how it got there: the span sequence and the deterministic
  attributes of every step (the order that was read, the evidence cited, the
  SQL that ran).

Identifiers minted per run and timestamps are normalized before comparison, so
a diff means the agent really changed its mind rather than the ids moving.

Replay needs state it can reproduce, so it always runs against a fresh
database seeded with the shipped fixtures. Traces recorded against data that
no longer exists will surface as diffs — that is the honest boundary, not a
bug in the replay.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.main import create_app
from app.models.conversation import Conversation, ConversationMessage
from app.models.observability import TraceSpan
from app.seed import seed_all

try:
    from evals import support_run
    from evals.fixtures import apply_fixtures
    from evals.run import _headers
except ModuleNotFoundError:  # `python evals/replay.py` puts evals/ on sys.path
    import support_run
    from fixtures import apply_fixtures
    from run import _headers

__all__ = ["main"]

_CONCLUSION_KEYS = (
    "intent",
    "clarified",
    "clarify_reason",
    "clarify_slot",
    "outcome",
    "action",
    "reply_sha256",
)

# A field that names a row minted this run cannot be compared across runs.
_VOLATILE_ATTRS = frozenset(
    {
        "approval_id",
        "created_at",
        "conversation_id",
        "duration_ms",
        "ended_at",
        "event_id",
        "id",
        "latency_ms",
        "query_id",
        "refund_id",
        "span_id",
        "started_at",
        "task_id",
        "ticket_id",
        "ticket_no",
        "trace_id",
    }
)

# Spans that make a turn impossible to reproduce without replaying side
# effects: a human took over, an approval was opened, or a write tool ran.
_BLOCKING_KINDS = frozenset({"approval", "handoff", "reconcile"})
# The analytics bookkeeping spans (`route`, `prefetch`, `expand`) are decision,
# budget and read-back records: no database write, no external call. Leaving
# them out would silently cut replayable coverage.
_READ_ONLY_TOOLS = frozenset(
    {
        "analytics.query",
        "analytics.route",
        "analytics.prefetch",
        "analytics.expand",
    }
)

# Row ids are minted when a database is seeded, so the same fixture can carry
# a different id in the replay database. They are compared as placeholders.
_MINTED_ID = re.compile(r"\b(?:doc|rfd|task|trc|conv|tkt|spn)_[0-9a-f]{6,}\b|\bTCK-[0-9A-F]{6,}\b")


class ReplayError(RuntimeError):
    """The replay could not be carried out (as opposed to disagreeing)."""


@dataclass(slots=True)
class RecordedTurn:
    message: str
    trace_id: str | None = None
    request_order_id: str | None = None


@dataclass(slots=True)
class RecordedConversation:
    conversation_id: str
    tenant_id: str
    user_id: str
    turns: list[RecordedTurn] = field(default_factory=list)


# ------------------------------------------------------------------ source
def _load_source(
    session: Any,
) -> tuple[list[RecordedConversation], dict[str, list[dict[str, Any]]]]:
    spans: dict[str, list[dict[str, Any]]] = {}
    span_rows = session.scalars(
        select(TraceSpan).order_by(TraceSpan.started_at, TraceSpan.id)
    ).all()
    for row in span_rows:
        spans.setdefault(row.trace_id, []).append(
            {
                "kind": row.kind,
                "name": row.name,
                "status": row.status,
                "attributes": row.attributes or {},
            }
        )

    traces_by_conversation: dict[str, list[str]] = {}
    for row in span_rows:
        if row.conversation_id is None:
            continue
        ids = traces_by_conversation.setdefault(row.conversation_id, [])
        if row.trace_id not in ids:
            ids.append(row.trace_id)

    conversations: list[RecordedConversation] = []
    rows = session.scalars(
        select(Conversation).order_by(Conversation.created_at, Conversation.id)
    ).all()
    for conversation in rows:
        messages = session.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation.id)
            .order_by(ConversationMessage.created_at, ConversationMessage.id)
        ).all()
        recorded = RecordedConversation(
            conversation_id=conversation.id,
            tenant_id=conversation.tenant_id,
            user_id=conversation.user_id,
        )
        pending_user: ConversationMessage | None = None
        for message in messages:
            if message.role == "user":
                pending_user = message
            elif message.role == "assistant" and pending_user is not None:
                payload = message.payload or {}
                user_payload = pending_user.payload or {}
                recorded.turns.append(
                    RecordedTurn(
                        message=pending_user.content,
                        trace_id=payload.get("trace_id"),
                        request_order_id=user_payload.get("order_id"),
                    )
                )
                pending_user = None
        # Before `trace_id` was stored on the assistant message, a trace could
        # only be paired with its turn by order in the conversation.
        known = traces_by_conversation.get(conversation.id, [])
        for index, turn in enumerate(recorded.turns):
            if turn.trace_id is None and index < len(known):
                turn.trace_id = known[index]
        if recorded.turns:
            conversations.append(recorded)
    return conversations, spans


def _ineligible_reason(
    turns: list[RecordedTurn], spans: dict[str, list[dict[str, Any]]]
) -> str | None:
    for turn in turns:
        if turn.trace_id is None:
            return "turn has no trace"
        recorded = spans.get(turn.trace_id, [])
        if not recorded:
            return "trace has no spans"
        kinds = {span["kind"] for span in recorded}
        blocked = kinds & _BLOCKING_KINDS
        if blocked:
            return f"has {'/'.join(sorted(blocked))} span"
        if not any(span["name"] == "support.reply" for span in recorded):
            return "no generate span"
        for span in recorded:
            if span["kind"] == "tool" and span["name"] not in _READ_ONLY_TOOLS:
                return f"write tool {span['name']}"
            if span["kind"] == "guard" and span["name"].startswith("tool.degraded."):
                return "degraded tool call"
    return None


# ------------------------------------------------------------------ compare
def _flatten(value: Any, prefix: str) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            flat.update(_flatten(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            flat.update(_flatten(item, f"{prefix}[{index}]"))
    else:
        flat[prefix] = value
    return flat


def _diff(label: str, expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    expected_flat = _flatten(expected, label)
    actual_flat = _flatten(actual, label)
    diffs: list[str] = []
    for key in sorted(set(expected_flat) | set(actual_flat)):
        if key not in actual_flat:
            diffs.append(f"{key}: missing in replay (recorded {expected_flat[key]!r})")
        elif key not in expected_flat:
            diffs.append(f"{key}: missing in recorded trace (replay {actual_flat[key]!r})")
        elif expected_flat[key] != actual_flat[key]:
            diffs.append(f"{key}: recorded {expected_flat[key]!r}, replay {actual_flat[key]!r}")
    return diffs


def _generate_span(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((span for span in reversed(spans) if span["name"] == "support.reply"), None)


def _conclusion(spans: list[dict[str, Any]]) -> dict[str, Any]:
    span = _generate_span(spans) or {"attributes": {}, "status": "MISSING"}
    attributes = span["attributes"]
    conclusion: dict[str, Any] = {key: attributes.get(key) for key in _CONCLUSION_KEYS}
    conclusion["status"] = span["status"]
    return conclusion


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, str):
        return _MINTED_ID.sub("<id>", value)
    return value


def _clean(attributes: dict[str, Any]) -> dict[str, Any]:
    return _normalize(
        {key: value for key, value in attributes.items() if key not in _VOLATILE_ATTRS}
    )


def _trajectory(spans: list[dict[str, Any]]) -> dict[str, Any]:
    classify = next((span for span in spans if span["name"] == "support.classify"), None)
    trajectory: dict[str, Any] = {
        "spans": [f"{span['kind']}/{span['name']}" for span in spans],
        "classify": _clean((classify or {}).get("attributes", {})),
        "retrieve": [
            _clean(span["attributes"]) for span in spans if span["name"] == "policy.search"
        ],
        "tools": [
            {
                "name": span["name"],
                "attributes": _clean(span["attributes"]),
            }
            for span in spans
            if span["kind"] == "tool"
        ],
        "guards": [
            {
                "name": span["name"],
                "attributes": _clean(span["attributes"]),
            }
            for span in spans
            if span["kind"] == "guard"
        ],
    }
    # Intent is the conclusion's field; repeating it here would double-report.
    trajectory["classify"].pop("intent", None)
    return trajectory


# ------------------------------------------------------------------- replay
def _new_app(database_url: str, *, seed: bool) -> FastAPI:
    settings = Settings(
        environment="test",
        database_url=database_url,
        auto_create_tables=True,
    )
    app = create_app(settings)
    if seed:
        seed_all(app.state.session_factory)
        apply_fixtures(app.state.session_factory)
    return app


def _replay_conversation(
    client: TestClient, conversation: RecordedConversation
) -> list[tuple[str | None, list[dict[str, Any]]]]:
    headers = _headers({"tenant": conversation.tenant_id, "user": conversation.user_id})
    conversation_id: str | None = None
    played: list[tuple[str | None, list[dict[str, Any]]]] = []
    for turn in conversation.turns:
        payload: dict[str, Any] = {"message": turn.message}
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        if turn.request_order_id:
            payload["order_id"] = turn.request_order_id
        response = client.post("/api/v1/support/messages", headers=headers, json=payload)
        if response.status_code != 200:
            raise ReplayError(
                f"{conversation.conversation_id}: replay turn failed with "
                f"{response.status_code}: {response.text[:200]}"
            )
        body = response.json()
        conversation_id = body.get("conversation_id")
        trace_id = body.get("trace_id")
        trace_response = client.get(f"/api/v1/traces/{trace_id}", headers=headers)
        if trace_response.status_code != 200:
            raise ReplayError(
                f"{conversation.conversation_id}: replayed trace {trace_id} not found"
            )
        played.append((trace_id, trace_response.json().get("spans") or []))
    return played


def _replay(
    conversations: list[RecordedConversation],
    spans: dict[str, list[dict[str, Any]]],
    database_url: str,
    *,
    trace_id: str | None,
    limit: int | None,
) -> list[str]:
    chosen: list[RecordedConversation] = []
    skipped: dict[str, int] = {}
    for conversation in conversations:
        if trace_id is not None and trace_id not in {turn.trace_id for turn in conversation.turns}:
            continue
        reason = _ineligible_reason(conversation.turns, spans)
        if reason is not None:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        chosen.append(conversation)
        if limit is not None and len(chosen) >= limit:
            break

    print(
        f"replay: {len(conversations)} recorded conversations, "
        f"{len(chosen)} replayable, {sum(skipped.values())} skipped"
    )
    for reason, count in sorted(skipped.items()):
        print(f"  skip {count}: {reason}")
    if trace_id is not None and not chosen:
        return [f"trace {trace_id}: not found or not replayable"]
    if not chosen:
        return ["no replayable conversations in the source"]

    app = _new_app(database_url, seed=True)
    client = TestClient(app)
    diffs: list[str] = []
    try:
        for conversation in chosen:
            played = _replay_conversation(client, conversation)
            if len(played) != len(conversation.turns):
                diffs.append(
                    f"{conversation.conversation_id}: recorded "
                    f"{len(conversation.turns)} turns, replayed {len(played)}"
                )
                continue
            for index, (recorded_turn, (new_trace, new_spans)) in enumerate(
                zip(conversation.turns, played, strict=True)
            ):
                recorded_spans = spans.get(recorded_turn.trace_id or "", [])
                conclusion = _diff(
                    "conclusion",
                    _conclusion(recorded_spans),
                    _conclusion(new_spans),
                )
                trajectory = _diff(
                    "trajectory",
                    _trajectory(recorded_spans),
                    _trajectory(new_spans),
                )
                if not conclusion and not trajectory:
                    continue
                diffs.append(
                    f"{conversation.conversation_id} turn[{index}] "
                    f"({recorded_turn.trace_id} -> {new_trace})"
                )
                diffs.extend(f"  CONCLUSION {item}" for item in conclusion)
                diffs.extend(f"  TRAJECTORY {item}" for item in trajectory)
    finally:
        app.state.engine.dispose()
    print(f"replay: {len(chosen)} conversations, {len(diffs)} diff lines")
    return diffs


def _generate_source(
    dataset: dict[str, Any], database_url: str
) -> tuple[list[RecordedConversation], dict[str, list[dict[str, Any]]]]:
    app = _new_app(database_url, seed=True)
    client = TestClient(app)
    try:
        failures: list[str] = []
        for case in dataset["cases"]:
            case_failures = support_run._run_conversation(app, client, case)
            if case_failures:
                failures.append(f"{case['id']}: {case_failures[0]}")
        if failures:
            raise ReplayError(
                "source dataset does not pass, so its traces are not a baseline: "
                + "; ".join(failures[:3])
            )
        with app.state.session_factory() as session:
            return _load_source(session)
    finally:
        app.state.engine.dispose()


def _print_diffs(diffs: list[str]) -> None:
    if not diffs:
        print("REPLAY DIFF EMPTY")
        return
    for item in diffs:
        print(item)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay recorded traces and diff the rerun against them."
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Record the support set in a temp database, replay it, and require an empty diff.",
    )
    parser.add_argument(
        "--database-url",
        help="Read traces from this database (otherwise combine with --self-check).",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).with_name("support_set.json"),
        help="Conversations used by --self-check to produce source traces.",
    )
    parser.add_argument("--trace-id", help="Only replay the conversation with this trace.")
    parser.add_argument("--limit", type=int, help="Replay at most this many conversations.")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as directory:
        if args.self_check or args.database_url is None:
            if args.database_url is not None:
                print("--self-check and --database-url are mutually exclusive")
                return 2
            dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
            source_url = f"sqlite+pysqlite:///{Path(directory) / 'source.db'}"
            try:
                conversations, spans = _generate_source(dataset, source_url)
            except ReplayError as exc:
                print(f"replay: {exc}")
                return 2
            if not conversations:
                print("self-check: the support set recorded no conversations")
                return 2
            replay_url = f"sqlite+pysqlite:///{Path(directory) / 'replay.db'}"
            try:
                diffs = _replay(
                    conversations,
                    spans,
                    replay_url,
                    trace_id=args.trace_id,
                    limit=args.limit,
                )
            except ReplayError as exc:
                print(f"replay: {exc}")
                return 2
            _print_diffs(diffs)
            if diffs:
                print("\nREPLAY SELF-CHECK FAILED")
                return 1
            print("REPLAY SELF-CHECK PASSED")
            return 0

        settings = Settings(environment="test", database_url=args.database_url)
        app = create_app(settings)
        try:
            with app.state.session_factory() as session:
                try:
                    conversations, spans = _load_source(session)
                except SQLAlchemyError as exc:
                    detail = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
                    print(f"replay: cannot read traces from the source: {detail}")
                    return 2
        finally:
            app.state.engine.dispose()
        replay_url = f"sqlite+pysqlite:///{Path(directory) / 'replay.db'}"
        try:
            diffs = _replay(
                conversations,
                spans,
                replay_url,
                trace_id=args.trace_id,
                limit=args.limit,
            )
        except ReplayError as exc:
            print(f"replay: {exc}")
            return 2
        _print_diffs(diffs)
        return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
