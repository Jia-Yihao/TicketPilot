"""The intent gap loop: dedup, human confirmation, corpus merge and report.

A missed utterance enters the queue once. The queue is keyed by a hash of the
normalized text, so case, whitespace and punctuation do not create a second
row, and tenant ids survive only as hashes. Model output may be recorded as a
proposal, but it never changes ``status`` or ``label``: only an explicit human
confirmation with a note can close a gap, and only closed gaps enter the
held-out corpus.

Everything here is deterministic and offline: no model, no network, no clock.
``render_*`` functions are the only serializers, so a rerun on the same input
produces the same bytes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from scripts.build_intent_set import LABELS, dedup_key, normalize

__all__ = [
    "BEFORE_METRICS_PATH",
    "CANDIDATES_PATH",
    "CLOSURES_PATH",
    "LABELS",
    "QUEUE_PATH",
    "REPORT_PATH",
    "STATUSES",
    "build_report",
    "closure_rows",
    "close",
    "dedup_key",
    "gap_id",
    "ignore",
    "ingest",
    "load_queue",
    "new_queue",
    "normalize",
    "propose",
    "render_closures",
    "render_queue",
    "render_report",
    "tenant_hash",
]

INTENT_DIR = Path(__file__).with_name("intent")
QUEUE_PATH = INTENT_DIR / "gap_queue.json"
CLOSURES_PATH = INTENT_DIR / "gap_closures.jsonl"
CANDIDATES_PATH = INTENT_DIR / "gap_candidates.jsonl"
# Metrics snapshot with the closure removed, so the cycle report's "before"
# side can be recomputed from the repository instead of a gitignored .tmp file.
BEFORE_METRICS_PATH = INTENT_DIR / "gap_metrics_before.json"
REPORT_PATH = INTENT_DIR / "gap_report.json"

VERSION = "2026-10-26"
STATUSES = ("OPEN", "CLOSED", "IGNORED")
_CYCLE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PROVENANCE_KEYS = ("dataset", "file", "line", "selection_id")
_LAYER_ORDER = ("external", "authored_gap")


# --------------------------------------------------------------- normalisation
def gap_id(text: str) -> str:
    return f"gap-{dedup_key(text)[:12]}"


def tenant_hash(tenant_id: str) -> str:
    return hashlib.sha256(f"tenant:{tenant_id}".encode()).hexdigest()


def _cycle(value: str) -> str:
    if not isinstance(value, str) or not _CYCLE.match(value):
        raise ValueError(f"cycle must look like YYYY-MM-DD, got {value!r}")
    return value


def _provenance(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in _PROVENANCE_KEYS if key in value}


def new_queue() -> dict[str, Any]:
    return {
        "version": VERSION,
        "description": (
            "Intent gaps sampled from traffic. One row per normalized text; "
            "tenants only as hashes; model output is a proposal, never gold."
        ),
        "normalization": (
            "NFKC, casefold, strip Unicode punctuation, collapse whitespace"
        ),
        "statuses": list(STATUSES),
        "cases": [],
    }


# -------------------------------------------------------------------- ingest
def _record_text(record: dict[str, Any]) -> str | None:
    text = record.get("text")
    if isinstance(text, str) and text.strip():
        return text
    for message in reversed(record.get("messages") or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return None


def _predicted(record: dict[str, Any]) -> str:
    # ``intent`` on a trace is the classifier's reading for that turn; when
    # both keys exist it is the stronger statement. ``predicted`` is for
    # hand-built candidate files.
    value = record.get("intent") or record.get("predicted") or "UNKNOWN"
    return str(value).upper()


def _add_tenant(case: dict[str, Any], value: str | None) -> None:
    if not value:
        return
    hashes = set(case["tenant_hashes"])
    hashes.add(value)
    case["tenant_hashes"] = sorted(hashes)


def ingest(
    queue: dict[str, Any],
    records: Iterable[dict[str, Any]],
    *,
    cycle: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Add sampled records, merging by normalized text.

    A record whose classifier reading is not ``UNKNOWN`` is not a gap and is
    skipped. An existing row only gains observations, tenants, signals and a
    newer ``last_cycle``; its status is never rewritten here.
    """
    _cycle(cycle)
    updated = copy.deepcopy(queue)
    by_key = {case["dedup_key"]: case for case in updated["cases"]}
    stats = {"added": 0, "merged": 0, "skipped": 0}

    for record in records:
        text = _record_text(record)
        if text is None or _predicted(record) != "UNKNOWN":
            stats["skipped"] += 1
            continue
        key = dedup_key(text)
        tenant = record.get("tenant_id")
        tenant_digest = tenant_hash(str(tenant)) if tenant not in (None, "") else None
        signals = sorted({str(item) for item in record.get("signals") or []})
        existing = by_key.get(key)
        if existing is not None:
            existing["observations"] += 1
            existing["last_cycle"] = max(existing["last_cycle"], cycle)
            _add_tenant(existing, tenant_digest)
            existing["signals"] = sorted(set(existing["signals"]) | set(signals))
            stats["merged"] += 1
            continue

        case = {
            "id": gap_id(text),
            "text": text,
            "dedup_key": key,
            "tenant_hashes": [tenant_digest] if tenant_digest else [],
            "status": "OPEN",
            "observations": 1,
            "first_cycle": cycle,
            "last_cycle": cycle,
            "signals": signals,
            "predicted": "UNKNOWN",
            "label": None,
            "note": "",
            "provenance": _provenance(record.get("provenance")),
            "confirmed_by": None,
            "confirmation": None,
            "closed_cycle": None,
            "proposals": [],
        }
        updated["cases"].append(case)
        by_key[key] = case
        stats["added"] += 1

    updated["cases"].sort(key=lambda case: case["id"])
    return updated, stats


# --------------------------------------------------------- proposals / humans
def _find(queue: dict[str, Any], wanted: str) -> dict[str, Any]:
    for case in queue["cases"]:
        if case["id"] == wanted:
            return case
    raise ValueError(f"unknown gap {wanted!r}")


def _require(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value.strip()


def propose(
    queue: dict[str, Any],
    wanted: str,
    *,
    label: str,
    note: str,
    by: str,
    cycle: str,
) -> dict[str, Any]:
    """Record a model suggestion without touching status, label or gold."""
    _cycle(cycle)
    if label not in LABELS:
        raise ValueError(f"unknown label {label!r}")
    proposal = {
        "source": "model",
        "label": label,
        "note": _require(note, "note"),
        "by": _require(by, "by"),
        "cycle": cycle,
    }
    updated = copy.deepcopy(queue)
    case = _find(updated, wanted)
    if case["status"] != "OPEN":
        raise ValueError(f"gap {wanted!r} is {case['status']}, not OPEN")
    case["proposals"].append(proposal)
    case["proposals"].sort(
        key=lambda item: (item["cycle"], item["by"], item["label"])
    )
    return updated


def _require_human(confirm_human: bool) -> None:
    if confirm_human is not True:
        raise ValueError("human confirmation required; pass --confirm-human")


def close(
    queue: dict[str, Any],
    wanted: str,
    *,
    label: str,
    note: str,
    by: str,
    cycle: str,
    confirm_human: bool,
) -> dict[str, Any]:
    _require_human(confirm_human)
    _cycle(cycle)
    if label not in LABELS:
        raise ValueError(f"unknown label {label!r}")
    clean_note = _require(note, "note")
    author = _require(by, "by")
    updated = copy.deepcopy(queue)
    case = _find(updated, wanted)
    if case["status"] != "OPEN":
        raise ValueError(f"gap {wanted!r} is {case['status']}, not OPEN")
    case.update(
        status="CLOSED",
        label=label,
        note=clean_note,
        confirmed_by=author,
        confirmation="human",
        closed_cycle=cycle,
    )
    return updated


def ignore(
    queue: dict[str, Any],
    wanted: str,
    *,
    note: str,
    by: str,
    cycle: str,
    confirm_human: bool,
) -> dict[str, Any]:
    _require_human(confirm_human)
    _cycle(cycle)
    clean_note = _require(note, "note")
    author = _require(by, "by")
    updated = copy.deepcopy(queue)
    case = _find(updated, wanted)
    if case["status"] != "OPEN":
        raise ValueError(f"gap {wanted!r} is {case['status']}, not OPEN")
    case.update(
        status="IGNORED",
        note=clean_note,
        confirmed_by=author,
        confirmation="human",
        closed_cycle=cycle,
    )
    return updated


def closure_rows(queue: dict[str, Any]) -> list[dict[str, Any]]:
    """Closed gaps as rows for the existing ``authored_gap`` layer."""
    rows: list[dict[str, Any]] = []
    for case in sorted(queue["cases"], key=lambda item: item["id"]):
        if case["status"] != "CLOSED":
            continue
        rows.append(
            {
                "id": case["id"],
                "text": case["text"],
                "gold": case["label"],
                "layer": "authored_gap",
                "label_source": "human",
                "note": case["note"],
                "provenance": {
                    "gap_id": case["id"],
                    "source": "intent-gap-loop",
                    "cycle": case["closed_cycle"],
                },
            }
        )
    return rows


# --------------------------------------------------------------------- report
def _snapshot(metrics: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    if not metrics:
        return None
    layer = (metrics.get("layers") or {}).get(name)
    if not layer:
        return None
    coverage = layer.get("coverage")
    return {
        "cases": int(layer["cases"]),
        "coverage": None if coverage is None else float(coverage),
        "false_unknown": int(layer["false_unknown"]),
    }


def _delta(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> dict[str, Any] | None:
    if before is None or after is None:
        return None
    coverage = None
    if before["coverage"] is not None and after["coverage"] is not None:
        coverage = round(after["coverage"] - before["coverage"], 4)
    return {
        "cases": after["cases"] - before["cases"],
        "coverage": coverage,
        "false_unknown": after["false_unknown"] - before["false_unknown"],
    }


def build_report(
    queue: dict[str, Any],
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    cycle: str | None = None,
) -> dict[str, Any]:
    cases = queue["cases"]
    counts = Counter(case["status"] for case in cases)
    names = [
        name
        for name in _LAYER_ORDER
        if _snapshot(before, name) is not None or _snapshot(after, name) is not None
    ]
    names.extend(
        sorted(
            {
                name
                for source in (before, after)
                if source
                for name in (source.get("layers") or {})
            }
            - set(names)
        )
    )
    layers: dict[str, Any] = {}
    for name in names:
        old = _snapshot(before, name)
        new = _snapshot(after, name)
        layers[name] = {
            "before": old,
            "after": new,
            "delta": _delta(old, new),
        }

    invariants = (after or {}).get("invariants") or {}
    failures = [str(item) for item in invariants.get("failures") or []]
    regression = bool(after) and invariants.get("ok") is False

    return {
        "version": VERSION,
        "cycle": cycle,
        "queue": {
            "total": len(cases),
            "OPEN": counts.get("OPEN", 0),
            "CLOSED": counts.get("CLOSED", 0),
            "IGNORED": counts.get("IGNORED", 0),
        },
        "new_cases": counts.get("CLOSED", 0),
        "closed_this_cycle": sum(
            1
            for case in cases
            if case["status"] == "CLOSED" and case["closed_cycle"] == cycle
        ),
        "ignored_this_cycle": sum(
            1
            for case in cases
            if case["status"] == "IGNORED" and case["closed_cycle"] == cycle
        ),
        "open_gaps": [
            {
                "id": case["id"],
                "text": case["text"],
                "observations": case["observations"],
                "first_cycle": case["first_cycle"],
                "last_cycle": case["last_cycle"],
                "proposals": len(case["proposals"]),
            }
            for case in cases
            if case["status"] == "OPEN"
        ],
        "layers": layers,
        "gate_regression": regression,
        "gate_failures": failures if regression else [],
        "note": (
            "Layers are reported separately; no single accuracy is computed. "
            "A closed gap is coverage evidence, not a real-world frequency."
        ),
    }


# -------------------------------------------------------------------- storage
def _validate_queue(queue: dict[str, Any]) -> None:
    if not isinstance(queue, dict):
        raise ValueError("queue must be an object")
    if queue.get("statuses") != list(STATUSES):
        raise ValueError(f"queue statuses must be {list(STATUSES)}")
    cases = queue.get("cases")
    if not isinstance(cases, list):
        raise ValueError("queue cases must be a list")

    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    for index, case in enumerate(cases):
        label = case.get("id", f"cases[{index}]")
        for key in (
            "id",
            "text",
            "dedup_key",
            "tenant_hashes",
            "status",
            "observations",
            "first_cycle",
            "last_cycle",
            "signals",
            "predicted",
            "label",
            "note",
            "provenance",
            "confirmed_by",
            "confirmation",
            "closed_cycle",
            "proposals",
        ):
            if key not in case:
                raise ValueError(f"{label}: missing {key!r}")
        if case["id"] in seen_ids:
            raise ValueError(f"{label}: duplicate id")
        if case["dedup_key"] in seen_keys:
            raise ValueError(f"{label}: duplicate dedup_key")
        if case["id"] != f"gap-{case['dedup_key'][:12]}":
            raise ValueError(f"{label}: id does not match dedup_key")
        if case["dedup_key"] != dedup_key(case["text"]):
            raise ValueError(f"{label}: dedup_key does not match text")
        if case["status"] not in STATUSES:
            raise ValueError(f"{label}: unknown status {case['status']!r}")
        _cycle(case["first_cycle"])
        _cycle(case["last_cycle"])
        if case["observations"] < 1:
            raise ValueError(f"{label}: observations must be positive")
        if case["predicted"] != "UNKNOWN":
            raise ValueError(f"{label}: predicted must be UNKNOWN")
        if case["status"] == "CLOSED":
            if case["label"] not in LABELS:
                raise ValueError(f"{label}: closed gap needs a known label")
            if not case["note"].strip():
                raise ValueError(f"{label}: closed gap needs a note")
            if case["confirmation"] != "human":
                raise ValueError(f"{label}: closed gap needs human confirmation")
            _cycle(case["closed_cycle"])
        elif case["status"] == "IGNORED":
            if case["label"] is not None:
                raise ValueError(f"{label}: an ignored gap has no gold label")
            if not case["note"].strip():
                raise ValueError(f"{label}: ignored gap needs a note")
            if case["confirmation"] != "human":
                raise ValueError(f"{label}: ignored gap needs human confirmation")
            _cycle(case["closed_cycle"])
        else:
            if case["label"] is not None:
                raise ValueError(f"{label}: an open gap has no gold label")
            if case["closed_cycle"] is not None:
                raise ValueError(f"{label}: an open gap has no closed_cycle")
        for digest in case["tenant_hashes"]:
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError(f"{label}: tenant hash must be sha256 hex")
        seen_ids.add(case["id"])
        seen_keys.add(case["dedup_key"])


def load_queue(path: Path = QUEUE_PATH) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    _validate_queue(data)
    return data


def render_queue(queue: dict[str, Any]) -> str:
    return json.dumps(queue, ensure_ascii=False, indent=2) + "\n"


def render_closures(rows: Sequence[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(row, ensure_ascii=False) + "\n" for row in rows
    )


def render_report(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
