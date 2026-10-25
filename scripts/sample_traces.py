"""Sample bad cases from the trace store, stratified by channel and intent.

A trace is where a failure becomes visible: an error span, a degraded tool,
a handoff, a clarification loop, a reply the output guard had to rewrite.
This script reads a time window of traces, scores each one, and writes a
JSONL queue that a human can work through — capped per channel x intent so
one noisy intent cannot crowd out the rest.

The queue is read-only and self-contained: every record carries the trace id,
the redacted transcript up to that turn, and the spans. A record can be
replayed with ``evals/replay.py --trace-id <id>`` against a database with the
same state.

Customer text is masked before it is written (phone, email, id and card
numbers), and the source database URL is never copied into the output.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import utcnow
from app.db.session import build_engine, build_session_factory
from app.models.conversation import ConversationMessage
from app.models.handoff import HandoffEvent
from app.models.observability import TraceSpan
from app.models.task import Task
from app.observability.redaction import redact, redact_payload

__all__ = [
    "signalize",
    "severity",
    "sample",
    "main",
]

# Handoff reasons that point at a specific failure the queue should name.
_HANDOFF_SIGNALS = {
    "REPEAT_FAILURE": "repeated_failure",
    "NO_UNDERSTANDING": "repeated_clarification",
    "NO_EVIDENCE": "no_evidence",
}

_SEVERITY_WEIGHT = {"high": 3, "medium": 2, "low": 1}
_HIGH_SIGNALS = frozenset(
    {
        "span_error",
        "task_failed",
        "task_timed_out",
        "task_rejected",
        "verification_failed",
        "handoff",
        "input_guard",
    }
)
_MEDIUM_SIGNALS = frozenset(
    {
        "degraded",
        "needs_human_review",
        "no_evidence",
        "repeated_clarification",
        "repeated_failure",
        "unsupported_claim",
    }
)


# ------------------------------------------------------------------ scoring
def signalize(
    spans: list[dict[str, Any]],
    task: dict[str, Any] | None,
    handoff: dict[str, Any] | None,
    *,
    slow_ms: int,
) -> list[str]:
    """Name every failure signal a trace carries, most specific first."""
    signals: list[str] = []
    if any(span["status"] != "OK" for span in spans):
        signals.append("span_error")
    for span in spans:
        name = span["name"]
        attributes = span.get("attributes") or {}
        if span["kind"] == "guard" and name.startswith("tool.degraded."):
            signals.append("degraded")
        if name == "handoff.escalate":
            signals.append("handoff")
            mapped = _HANDOFF_SIGNALS.get(str(attributes.get("reason") or ""))
            if mapped:
                signals.append(mapped)
        if name == "support.reply" and attributes.get("clarified"):
            signals.append("clarification")
        if name == "output.grounded" and int(attributes.get("replacements") or 0) > 0:
            signals.append("unsupported_claim")
        if name == "support.classify" and attributes.get("guard_categories"):
            signals.append("input_guard")
    if handoff is not None and "handoff" not in signals:
        signals.append("handoff")
        mapped = _HANDOFF_SIGNALS.get(str(handoff.get("reason") or ""))
        if mapped:
            signals.append(mapped)
    if task is not None:
        status = task.get("status")
        if status == "FAILED":
            signals.append("task_failed")
        elif status == "TIMED_OUT":
            signals.append("task_timed_out")
        elif status == "REJECTED":
            signals.append("task_rejected")
        outcome = (task.get("result") or {}).get("outcome")
        if outcome == "FAILED":
            signals.append("verification_failed")
        elif outcome == "NEEDS_HUMAN_REVIEW":
            signals.append("needs_human_review")
    latency = sum(int(span.get("latency_ms") or 0) for span in spans)
    if latency >= slow_ms:
        signals.append("slow")
    return list(dict.fromkeys(signals))


def severity(signals: list[str]) -> str:
    if any(item in _HIGH_SIGNALS for item in signals):
        return "high"
    if any(item in _MEDIUM_SIGNALS for item in signals):
        return "medium"
    return "low"


def _score(signals: list[str]) -> int:
    return sum(_SEVERITY_WEIGHT[severity([item])] for item in signals)


# ------------------------------------------------------------------- loading
def _load_spans(
    session: Session, *, since: Any, tenant: str | None
) -> dict[str, list[dict[str, Any]]]:
    statement = select(TraceSpan).order_by(TraceSpan.started_at, TraceSpan.id)
    if since is not None:
        statement = statement.where(TraceSpan.started_at >= since)
    if tenant is not None:
        statement = statement.where(TraceSpan.tenant_id == tenant)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in session.scalars(statement):
        grouped.setdefault(row.trace_id, []).append(
            {
                "kind": row.kind,
                "name": row.name,
                "status": row.status,
                "attributes": redact_payload(row.attributes or {}),
                "latency_ms": row.latency_ms,
                "started_at": row.started_at.isoformat(),
                "conversation_id": row.conversation_id,
                "tenant_id": row.tenant_id,
            }
        )
    return grouped


def _load_tasks(session: Session, trace_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not trace_ids:
        return {}
    rows = session.scalars(select(Task).where(Task.trace_id.in_(trace_ids))).all()
    return {
        str(row.trace_id): {
            "task_id": row.id,
            "status": str(row.status),
            "context": row.context or {},
            "result": row.result,
        }
        for row in rows
        if row.trace_id
    }


def _load_handoffs(session: Session, trace_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not trace_ids:
        return {}
    rows = session.scalars(select(HandoffEvent).where(HandoffEvent.trace_id.in_(trace_ids))).all()
    return {
        str(row.trace_id): {
            "reason": str(row.reason),
            "signal_id": row.signal_id,
            "status": str(row.status),
            "intent": row.intent,
            "channel": (row.bundle or {}).get("channel"),
        }
        for row in rows
        if row.trace_id
    }


def _load_transcripts(
    session: Session,
    trace_order: dict[str, list[str]],
    conversation_ids: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """Map each support trace to the transcript that produced it."""
    if not conversation_ids:
        return {}
    rows = session.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id.in_(conversation_ids))
        .order_by(ConversationMessage.created_at, ConversationMessage.id)
    ).all()
    per_conversation: dict[str, list[ConversationMessage]] = {}
    for row in rows:
        per_conversation.setdefault(row.conversation_id, []).append(row)

    transcripts: dict[str, list[dict[str, Any]]] = {}
    for conversation_id, messages in per_conversation.items():
        traces = trace_order.get(conversation_id, [])
        transcript: list[dict[str, Any]] = []
        cursor = 0
        for message in messages:
            transcript.append(
                {
                    "role": message.role,
                    "intent": message.intent,
                    "content": redact(message.content or ""),
                    "created_at": message.created_at.isoformat(),
                }
            )
            if message.role != "assistant":
                continue
            payload = message.payload or {}
            trace_id = payload.get("trace_id")
            if trace_id is None and cursor < len(traces):
                # Traces recorded before the message carried its trace id can
                # only be paired by order within the conversation.
                trace_id = traces[cursor]
            cursor += 1
            if trace_id is not None:
                transcripts[str(trace_id)] = [dict(item) for item in transcript]
    return transcripts


def _intent(spans: list[dict[str, Any]], handoff: dict[str, Any] | None) -> str:
    if handoff is not None and handoff.get("intent"):
        return str(handoff["intent"])
    for name in ("support.reply", "support.classify", "task.route"):
        span = next((item for item in spans if item["name"] == name), None)
        intent = (span or {}).get("attributes", {}).get("intent")
        if intent:
            return str(intent)
    return "UNKNOWN"


def _channel(handoff: dict[str, Any] | None, task: dict[str, Any] | None) -> str:
    if handoff is not None and handoff.get("channel"):
        return str(handoff["channel"]).upper()
    if task is not None:
        channel = (task.get("context") or {}).get("channel")
        if isinstance(channel, str) and channel:
            return channel.upper()
    return "WEB"


# ------------------------------------------------------------------ sampling
def sample(
    records: list[dict[str, Any]], *, per_stratum: int, limit: int | None
) -> tuple[list[dict[str, Any]], dict[str, tuple[int, int]]]:
    """Cap the queue per channel x intent, keeping the worst cases first."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        key = f"{record['channel']}/{record['intent']}"
        buckets.setdefault(key, []).append(record)

    chosen: list[dict[str, Any]] = []
    report: dict[str, tuple[int, int]] = {}
    for key in sorted(buckets):
        candidates = sorted(
            buckets[key],
            key=lambda item: (-int(item["score"]), str(item["started_at"]), item["trace_id"]),
        )
        kept = candidates[:per_stratum]
        for record in kept:
            record["stratum"] = key
        chosen.extend(kept)
        report[key] = (len(candidates), len(kept))

    chosen.sort(key=lambda item: (-int(item["score"]), str(item["started_at"]), item["trace_id"]))
    if limit is not None:
        chosen = chosen[:limit]
    return chosen, report


# --------------------------------------------------------------------- main
def _record(
    trace_id: str,
    spans: list[dict[str, Any]],
    task: dict[str, Any] | None,
    handoff: dict[str, Any] | None,
    transcript: list[dict[str, Any]],
    signals: list[str],
) -> dict[str, Any]:
    first = spans[0]
    started_at = str(first.get("started_at") or "")
    return {
        "trace_id": trace_id,
        "conversation_id": first.get("conversation_id"),
        "tenant_id": first.get("tenant_id"),
        "channel": _channel(handoff, task),
        "intent": _intent(spans, handoff),
        "signals": signals,
        "severity": severity(signals),
        "score": _score(signals),
        "task_status": (task or {}).get("status"),
        "outcome": ((task or {}).get("result") or {}).get("outcome"),
        "started_at": started_at,
        "latency_ms": sum(int(span.get("latency_ms") or 0) for span in spans),
        "messages": transcript,
        "spans": [
            {
                "kind": span["kind"],
                "name": span["name"],
                "status": span["status"],
                "latency_ms": span["latency_ms"],
                "attributes": span["attributes"],
            }
            for span in spans
        ],
        "replay": {
            "command": (
                f"python evals/replay.py --database-url <database-url> --trace-id {trace_id}"
            )
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sample a bad-case queue from persisted traces.")
    parser.add_argument(
        "--database-url",
        help="Database to read (defaults to the configured DATABASE_URL).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".tmp/bad_cases.jsonl"),
        help="JSONL file to write.",
    )
    parser.add_argument("--tenant", help="Only sample this tenant.")
    parser.add_argument(
        "--since-minutes",
        type=int,
        default=24 * 60,
        help="Look back this many minutes (default 1440). Use 0 for no limit.",
    )
    parser.add_argument(
        "--slow-ms",
        type=int,
        default=3000,
        help="A trace whose spans add up to this many milliseconds is slow.",
    )
    parser.add_argument(
        "--per-stratum",
        type=int,
        default=3,
        help="Keep at most this many cases per channel x intent.",
    )
    parser.add_argument("--limit", type=int, default=100, help="Cap the whole queue.")
    args = parser.parse_args(argv)

    database_url = args.database_url or Settings().database_url
    since = utcnow() - timedelta(minutes=args.since_minutes) if args.since_minutes > 0 else None

    engine = None
    try:
        engine = build_engine(database_url)
        factory = build_session_factory(engine)
        with factory() as session:
            grouped = _load_spans(session, since=since, tenant=args.tenant)
            trace_ids = list(grouped)
            tasks = _load_tasks(session, trace_ids)
            handoffs = _load_handoffs(session, trace_ids)

            candidates: list[dict[str, Any]] = []
            trace_order: dict[str, list[str]] = {}
            for trace_id, spans in grouped.items():
                conversation_id = spans[0]["conversation_id"]
                if conversation_id:
                    trace_order.setdefault(conversation_id, []).append(trace_id)
                signals = signalize(
                    spans,
                    tasks.get(trace_id),
                    handoffs.get(trace_id),
                    slow_ms=args.slow_ms,
                )
                if signals:
                    candidates.append(
                        _record(
                            trace_id,
                            spans,
                            tasks.get(trace_id),
                            handoffs.get(trace_id),
                            [],
                            signals,
                        )
                    )

            transcripts = _load_transcripts(
                session,
                trace_order,
                {
                    str(record["conversation_id"])
                    for record in candidates
                    if record["conversation_id"]
                },
            )
    except SQLAlchemyError as exc:
        # The driver's full message includes the failing statement; the first
        # line names the problem without echoing the query.
        detail = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        print(f"sample: cannot read traces: {detail}")
        return 2
    finally:
        if engine is not None:
            engine.dispose()

    for record in candidates:
        record["messages"] = transcripts.get(record["trace_id"], [])

    chosen, report = sample(candidates, per_stratum=args.per_stratum, limit=args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in chosen:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(
        f"sample: {len(grouped)} traces, {len(candidates)} bad cases, "
        f"{len(chosen)} queued -> {args.output}"
    )
    for key in sorted(report):
        found, kept = report[key]
        print(f"  {key}: {kept}/{found}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
