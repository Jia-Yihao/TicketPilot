"""Trace recording: the seam that lets a request be replayed afterwards.

A trace is one request. It carries a tree of spans, one per meaningful step:
``understand`` (what the user wanted), ``retrieve`` (what evidence was
found), ``tool`` (what was called), ``approval`` (who intervened),
``handoff`` (when a human took over).

The recorder never raises into the request path: a broken trace must not turn
a successful answer into a failed one. Spans are buffered in memory and
flushed by the caller, so a span can be opened before the work and closed
after it.
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

from app.observability.redaction import redact_payload

__all__ = ["SpanKind", "SpanBuffer", "TraceRecorder", "elapsed_ms", "reply_digest"]

_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_GENERATED_REF = re.compile(
    r"\b(?:rfd|task|trc|conv|tkt|spn)_[0-9a-f]{6,}\b|\bTCK-[0-9A-F]{6,}\b"
)


def elapsed_ms(started: float) -> int:
    """Whole milliseconds elapsed since a ``time.perf_counter()`` reading.

    The one place the span-latency formula lives (R9 口径 §2.1、§3.4): every
    segment producer and every timed block reports through here, so a reader
    can trust that two spans measured the same way. Call sites must go through
    the module attribute (``tracing.elapsed_ms(...)``) so tests can stub this
    one symbol. Never clamp or round here: a wrong start reading must stay
    observable, not be smoothed into a plausible number.
    """
    return int((time.perf_counter() - started) * 1000)


def reply_digest(text: str) -> str:
    """Hash a reply with per-run identifiers normalized away.

    Replay compares two runs of the same turn; minted ids and timestamps
    differ by construction, so they are replaced before hashing. Everything
    else — wording, amounts, policy sections — stays in the digest, which is
    what makes a changed answer detectable from the trace alone.
    """
    normalized = _ISO_TIMESTAMP.sub("<ts>", text)
    normalized = _GENERATED_REF.sub("<ref>", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

SpanKind = Literal[
    "guard",
    "understand",
    "retrieve",
    "generate",
    "tool",
    "approval",
    "handoff",
    "reconcile",
    "root",
]


@dataclass(slots=True)
class SpanBuffer:
    """One finished span, waiting to be written."""

    id: str
    kind: str
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    parent_span_id: str | None = None
    status: str = "OK"
    latency_ms: int = 0


class TraceRecorder:
    """Collects the spans of one request and hands them to a writer."""

    def __init__(
        self,
        *,
        tenant_id: str,
        trace_id: str | None = None,
        conversation_id: str | None = None,
        root_name: str | None = None,
    ) -> None:
        self.trace_id = trace_id or f"trc_{uuid.uuid4().hex[:16]}"
        self.tenant_id = tenant_id
        self.conversation_id = conversation_id
        self.root_name = root_name
        self.spans: list[SpanBuffer] = []
        self._open: list[SpanBuffer] = []

    # ------------------------------------------------------------- writing
    def add(
        self,
        kind: str,
        name: str,
        *,
        attributes: dict[str, Any] | None = None,
        status: str = "OK",
        latency_ms: int = 0,
    ) -> SpanBuffer:
        """Record a finished span."""
        return self._record(
            kind,
            name,
            attributes=attributes or {},
            status=status,
            latency_ms=latency_ms,
            parent_span_id=self._open[-1].id if self._open else None,
        )

    @contextmanager
    def span(
        self, kind: str, name: str, *, attributes: dict[str, Any] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Time a block and record it as a span.

        The yielded dict is filled in by the caller; whatever it holds when
        the block ends becomes the span attributes. Nested spans point at the
        span that opened them.
        """
        payload: dict[str, Any] = dict(attributes or {})
        span_id = f"spn_{uuid.uuid4().hex[:16]}"
        parent_span_id = self._open[-1].id if self._open else None
        self._open.append(SpanBuffer(id=span_id, kind=kind, name=name))
        started = time.perf_counter()
        try:
            yield payload
        except Exception:
            self._record(
                kind,
                name,
                attributes={**payload, "error": True},
                status="ERROR",
                latency_ms=self._elapsed_ms(started),
                span_id=span_id,
                parent_span_id=parent_span_id,
            )
            raise
        else:
            self._record(
                kind,
                name,
                attributes=payload,
                status="OK",
                latency_ms=self._elapsed_ms(started),
                span_id=span_id,
                parent_span_id=parent_span_id,
            )
        finally:
            self._open.pop()

    # ------------------------------------------------------------- finalize
    def finalize(self) -> None:
        """Give the tree its one root span before the writer flushes it.

        The only birthplace of a ``root`` span in the codebase (R9 口径 §2.3):
        no caller ever adds a root span itself, so "one root per tree"
        is a property of this method, not of caller discipline. Idempotent —
        a second call changes nothing (§2.3.6). A recorder built without a
        root name grows no root (§7.1), so existing callers are untouched.

        The root is appended last so readers that order by insertion (or by
        ``started_at, id``) still see the segment spans first (§2.3.7). Its
        attributes are the empty dict, still routed through ``_record`` so
        they pass the same redaction path as every other span (§2.3.8).
        Never raise here: the recorder's standing promise is that a broken
        trace must not fail a request (§2.3.5) — an unexpectedly open stack is
        swallowed, observable only by unit tests asserting ``_open`` is empty.
        """
        if self.root_name is None:
            return
        for span in self.spans:
            if span.kind == "root":
                return  # already finalized: idempotent (§2.3.6)
        root = self._record(
            "root",
            self.root_name,
            attributes={},
            status="OK",
            latency_ms=0,
            parent_span_id=None,
        )
        for span in self.spans:
            if span is root:
                continue
            if span.parent_span_id is None:
                span.parent_span_id = root.id  # rehang top-level spans only

    # ------------------------------------------------------------- queries
    def kinds(self) -> list[str]:
        return [span.kind for span in self.spans]

    def find(self, kind: str) -> SpanBuffer | None:
        for span in self.spans:
            if span.kind == kind:
                return span
        return None

    def attributes_of(self, kind: str) -> dict[str, Any]:
        span = self.find(kind)
        return dict(span.attributes) if span is not None else {}

    # ------------------------------------------------------------ internals
    def _record(
        self,
        kind: str,
        name: str,
        *,
        attributes: dict[str, Any],
        status: str,
        latency_ms: int,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> SpanBuffer:
        span = SpanBuffer(
            id=span_id or f"spn_{uuid.uuid4().hex[:16]}",
            kind=kind,
            name=name,
            attributes=redact_payload(attributes),
            parent_span_id=parent_span_id,
            status=status,
            latency_ms=latency_ms,
        )
        self.spans.append(span)
        return span

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return elapsed_ms(started)
