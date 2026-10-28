"""The server-computed facts one statistics question is answered from.

A snapshot is the only thing the expression model is allowed to see. It has
three layers:

* **L1** — the aggregate: count, sum and per-bucket numbers, already computed
  by Python or by a scoped SQL aggregate;
* **L2** — a short, deterministic list of the most recent rows;
* **L3** — the rows themselves, kept out of the prompt and returned only when
  the customer asks to see them (the ``query_id`` is the key).

L1 and L2 share a hard character budget. Trimming is deterministic: the
oldest L2 row goes first, and the note says what was left out, so two runs on
the same data produce the same context.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.security import Identity
from app.core.time import utcnow

__all__ = [
    "AnalyticsSnapshot",
    "FactGroup",
    "RecentFact",
    "SnapshotStore",
    "estimate_tokens",
]


@dataclass(frozen=True, slots=True)
class FactGroup:
    """One bucket of a grouped answer, in the dataset's own vocabulary."""

    bucket: str
    label: str
    count: int
    total_cents: int


@dataclass(frozen=True, slots=True)
class RecentFact:
    """One recent row, reduced to fields that are safe to express."""

    at: str
    status: str
    amount_cents: int | None
    reference: str


@dataclass(frozen=True, slots=True)
class AnalyticsSnapshot:
    """Facts, layers and the key that can fetch the detail later."""

    query_id: str
    dataset: str
    label: str
    unit: str
    metric: str
    group_by: str | None
    time_range: str
    count: int
    total_cents: int
    groups: tuple[FactGroup, ...]
    recent: tuple[RecentFact, ...]
    rows: tuple[dict[str, Any], ...]
    total_rows: int
    truncated: bool
    created_at: datetime
    expires_at: datetime
    context_budget: int = 1400

    # ------------------------------------------------------------------ L1
    def l1(self) -> dict[str, Any]:
        facts: dict[str, Any] = {
            "dataset": self.label,
            "range": self.time_range,
            "metric": self.metric,
        }
        if self.metric in {"count", "count_and_sum"}:
            facts["count"] = self.count
        if self.metric in {"sum", "count_and_sum"}:
            facts["total_yuan"] = _yuan(self.total_cents)
        if self.groups:
            facts["groups"] = [
                _group_fact(group, self.metric) for group in self.groups
            ]
        return facts

    def l1_rows(self) -> tuple[dict[str, Any], ...]:
        """The same shape the controlled SQL path returns, for equality tests."""
        rows: list[dict[str, Any]] = []
        if self.group_by is None:
            row: dict[str, Any] = {}
            if self.metric in {"count", "count_and_sum"}:
                row["metric"] = self.count
            if self.metric in {"sum", "count_and_sum"}:
                row["metric_total"] = self.total_cents
            return (row,)
        for group in self.groups:
            row = {"bucket": group.bucket}
            if self.metric in {"count", "count_and_sum"}:
                row["metric"] = group.count
            if self.metric in {"sum", "count_and_sum"}:
                row["metric_total"] = group.total_cents
            rows.append(row)
        return tuple(rows)

    # ------------------------------------------------------------ L1 + L2
    def prompt_context(self, *, max_chars: int | None = None) -> dict[str, Any]:
        """The facts the expression model may see; L3 is never included."""
        budget = max_chars or self.context_budget
        facts = self.l1()
        recent = [_recent_fact(item) for item in self.recent]
        notes: list[str] = []
        if self.truncated:
            notes.append(
                f"只展开最近 {len(self.rows)} 条明细，统计数字仍是全量。"
            )
        while True:
            if recent:
                facts["recent"] = recent
            else:
                facts.pop("recent", None)
            if notes:
                facts["note"] = "".join(notes)
            payload = _dump(facts)
            if len(payload) <= budget:
                break
            if recent:
                recent.pop()
                continue
            if facts.get("groups"):
                groups = list(facts["groups"])
                groups.pop()
                facts["groups"] = groups
                notes = ["明细与分组按上下文预算截断，统计总数仍是全量。"]
                continue
            # The aggregate itself is the floor; it is never dropped.
            break
        return {
            "facts": facts,
            "chars": len(payload),
            "estimated_tokens": estimate_tokens(payload),
        }


class SnapshotStore:
    """Process-local L3 rows, keyed by ``query_id`` and bound to an identity.

    Multi-worker deployments can miss a snapshot created by another worker;
    the caller then asks the customer to re-ask. That is a declared boundary,
    not a silent fallback to stale rows.
    """

    def __init__(self) -> None:
        self._items: dict[str, tuple[str, str, AnalyticsSnapshot]] = {}
        self._lock = threading.Lock()

    def put(self, snapshot: AnalyticsSnapshot, identity: Identity) -> None:
        with self._lock:
            # A new question is the natural point to drop rows nobody can
            # open any more; otherwise the process keeps every expired L3
            # until the same id happens to be read again.
            expired = [
                key
                for key, (_, _, item) in self._items.items()
                if item.expires_at <= snapshot.created_at
            ]
            for key in expired:
                self._items.pop(key, None)
            self._items[snapshot.query_id] = (
                identity.tenant_id,
                identity.user_id,
                snapshot,
            )

    def get(
        self,
        query_id: str,
        identity: Identity,
        *,
        now: datetime | None = None,
    ) -> AnalyticsSnapshot | None:
        with self._lock:
            item = self._items.get(query_id)
            if item is None:
                return None
            tenant_id, user_id, snapshot = item
            if tenant_id != identity.tenant_id or user_id != identity.user_id:
                return None
            if snapshot.expires_at <= (now or utcnow()):
                self._items.pop(query_id, None)
                return None
            return snapshot

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


def estimate_tokens(text: str) -> int:
    """A deterministic, deliberately conservative token estimate.

    Two characters per token is an upper bound for the mixed Chinese/JSON
    context this path produces; the budget is stated in these units so a
    test does not depend on a model's tokenizer.
    """
    return math.ceil(len(text) / 2)


def _group_fact(group: FactGroup, metric: str) -> dict[str, Any]:
    fact: dict[str, Any] = {"bucket": group.label}
    if metric in {"count", "count_and_sum"}:
        fact["count"] = group.count
    if metric in {"sum", "count_and_sum"}:
        fact["total_yuan"] = _yuan(group.total_cents)
    return fact


def _recent_fact(item: RecentFact) -> dict[str, Any]:
    fact: dict[str, Any] = {"at": item.at, "status": item.status}
    if item.amount_cents is not None:
        fact["amount_yuan"] = _yuan(item.amount_cents)
    fact["reference"] = item.reference
    return fact


def _yuan(cents: int) -> str:
    return f"{cents / 100:.2f}"


def _dump(facts: dict[str, Any]) -> str:
    return json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
