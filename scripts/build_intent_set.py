"""Merge the two intent-evaluation layers into one committed dataset.

The layers stay separate on purpose:

* ``external`` — real user phrasings sampled from a public customer-service
  corpus, labelled by hand. It is evidence about wording we did not write.
* ``authored_gap`` — phrasings written to fill the intents the external corpus
  does not contain (``SELF_ANALYTICS``) or contains too few of, plus intent
  gaps closed by a human through ``scripts/manage_intent_gaps.py``. It is
  evidence about coverage, not about real-world frequency.

The merged file is the only input the metrics script reads. ``--check``
recomputes it and compares byte for byte, including the external sample hash,
so a hand-edited dataset cannot drift from its sources.

Usage::

    python scripts/build_intent_set.py
    python scripts/build_intent_set.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INTENT_DIR = ROOT / "evals" / "intent"
EXTERNAL_PATH = INTENT_DIR / "external_phrasings.jsonl"
EXTERNAL_MANIFEST_PATH = INTENT_DIR / "external_manifest.json"
AUTHORED_PATH = INTENT_DIR / "authored_gap.jsonl"
CLOSURES_PATH = INTENT_DIR / "gap_closures.jsonl"
DATASET_PATH = INTENT_DIR / "intent_set.json"

VERSION = "2026-10-26"
LABELS = (
    "HUMAN_HANDOFF",
    "REFUND_REQUEST",
    "TICKET_CREATE",
    "REFUND_STATUS",
    "REFUND_ELIGIBILITY",
    "POLICY_QA",
    "DELIVERY_ISSUE",
    "LOGISTICS_QUERY",
    "SELF_ANALYTICS",
    "PRODUCT_QUERY",
    "ORDER_QUERY",
    "UNKNOWN",
)
MIN_PER_INTENT = 10

LAYER_ORDER = {"external": 0, "authored_gap": 1}
LAYER_NOTES = {
    "external": (
        "Real user phrasings from the public corpus named in external_manifest.json, "
        "labelled by hand. The corpus carries no usable intent labels; every gold "
        "value here was written against app/agent/intents.yaml."
    ),
    "authored_gap": (
        "Phrasings written for the intents the external layer does not contain or "
        "contains too few of, plus gaps closed by a human through the intent gap "
        "queue. Reported separately: it is coverage evidence, not a real-world "
        "frequency estimate."
    ),
}
INTENT_NOTES = {
    "SELF_ANALYTICS": (
        "Absent from the external layer: the corpus is a product Q&A stream whose "
        "users ask about one order at a time, and it contains no aggregate question "
        "about the caller's own records. All evidence for this intent is authored."
    ),
    "HUMAN_HANDOFF": (
        "The external layer has 10 cases, but they say '店长/负责人/管事的/客服电话' "
        "rather than the catalog's '转人工' wording; that is why the intent is kept "
        "in the table with a real denominator instead of being dropped."
    ),
}
LABEL_RULE = (
    "Gold is the capability that should serve the request, read against "
    "app/agent/intents.yaml, not against the classifier's cue table. A request to "
    "move money is REFUND_REQUEST even when it names an order; a request to register "
    "a case is TICKET_CREATE even when the cause is a delivery problem; a question "
    "about a rule is POLICY_QA even when no policy document covers the topic, because "
    "the no-evidence path is the expected behaviour. When a sentence could be about "
    "order fields or transport position, ORDER_QUERY covers fields (payment, address, "
    "amount) and LOGISTICS_QUERY covers where the parcel is. REFUND_ELIGIBILITY "
    "asks whether this order or return qualifies and usually names the user's own "
    "item or action; POLICY_QA asks what the rule says in general. UNKNOWN means no "
    "catalog capability should serve it, not 'the classifier is unsure'."
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path, layer: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{number}: invalid JSON: {exc}") from exc
        if row.get("layer") != layer:
            raise SystemExit(f"{path}:{number}: layer must be {layer!r}")
        rows.append(row)
    return rows


def _validate(rows: list[dict[str, Any]], where: str) -> None:
    seen_ids: set[str] = set()
    seen_text: set[str] = set()
    for index, row in enumerate(rows):
        label = row.get("id", f"{where}[{index}]")
        for key in ("id", "text", "gold", "layer", "label_source", "note"):
            if key not in row:
                raise SystemExit(f"{label}: missing {key!r}")
        if row["gold"] not in LABELS:
            raise SystemExit(f"{label}: unknown gold {row['gold']!r}")
        if row["label_source"] != "human":
            raise SystemExit(f"{label}: label_source must be human")
        if not row["text"].strip():
            raise SystemExit(f"{label}: empty text")
        if row["id"] in seen_ids:
            raise SystemExit(f"{label}: duplicate id")
        if row["text"] in seen_text:
            raise SystemExit(f"{label}: duplicate text {row['text']!r}")
        seen_ids.add(row["id"])
        seen_text.add(row["text"])


def _external_rows() -> list[dict[str, Any]]:
    rows = _load_jsonl(EXTERNAL_PATH, "external")
    _validate(rows, "external")
    for row in rows:
        provenance = row.get("provenance") or {}
        if not {"dataset", "file", "line"} <= set(provenance):
            raise SystemExit(f"{row['id']}: external row needs full provenance")
    return rows


def _authored_rows() -> list[dict[str, Any]]:
    rows = _load_jsonl(AUTHORED_PATH, "authored_gap")
    _validate(rows, "authored_gap")
    for row in rows:
        row["provenance"] = {"authored": "intent-corpus-eval-2026-10"}
    return rows


def _closure_rows() -> list[dict[str, Any]]:
    if not CLOSURES_PATH.exists():
        raise SystemExit(f"missing {CLOSURES_PATH}")
    rows = _load_jsonl(CLOSURES_PATH, "authored_gap")
    _validate(rows, "authored_gap")
    for row in rows:
        provenance = row.get("provenance") or {}
        if provenance.get("source") != "intent-gap-loop":
            raise SystemExit(f"{row['id']}: closure needs its gap provenance")
        if provenance.get("gap_id") != row["id"]:
            raise SystemExit(f"{row['id']}: gap_id must equal the case id")
        if not provenance.get("cycle"):
            raise SystemExit(f"{row['id']}: closure needs its cycle")
        if not row["id"].startswith("gap-"):
            raise SystemExit(f"{row['id']}: closure id must start with gap-")
    return rows


def normalize(text: str) -> str:
    """NFKC, casefold, drop punctuation, collapse whitespace.

    This is the same normalisation the gap queue uses. Sharing it here keeps
    "one sentence" defined once: a closure that only differs from an existing
    row by punctuation or case would otherwise be scored twice.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    parts: list[str] = []
    for char in folded:
        if char.isspace():
            parts.append(" ")
            continue
        if unicodedata.category(char)[0] in {"P", "Z", "C"}:
            continue
        parts.append(char)
    return " ".join("".join(parts).split())


def dedup_key(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def assert_unique_texts(rows: list[dict[str, Any]]) -> None:
    """No text may appear twice, including across layers.

    The layers answer different questions, but a duplicated sentence would
    count once for wording evidence and once for coverage evidence. It is
    rejected instead of being scored twice. Comparison uses the queue's
    normalisation, so a punctuation-only variant cannot slip through.
    """
    seen: dict[str, str] = {}
    for row in rows:
        text = normalize(row["text"])
        label = str(row.get("id", "?"))
        if text in seen:
            raise SystemExit(
                f"duplicate text across layers: {label} and {seen[text]}"
            )
        seen[text] = label


def build() -> dict[str, Any]:
    external = _external_rows()
    authored = _authored_rows() + _closure_rows()
    cases = external + authored
    assert_unique_texts(cases)
    cases.sort(key=lambda row: (LAYER_ORDER[row["layer"]], row["id"]))

    per_layer = Counter(row["layer"] for row in cases)
    per_intent = Counter(row["gold"] for row in cases)
    missing = [label for label in LABELS if per_intent[label] < MIN_PER_INTENT]
    if missing:
        raise SystemExit(f"intents below {MIN_PER_INTENT} cases: {missing}")

    external_manifest = json.loads(EXTERNAL_MANIFEST_PATH.read_text(encoding="utf-8"))
    if external_manifest.get("sample_sha256") != _digest(EXTERNAL_PATH):
        raise SystemExit("external_phrasings.jsonl does not match its manifest")

    return {
        "version": VERSION,
        "description": (
            "Held-out intent corpus in two layers: real external phrasings and an "
            "authored gap layer. Report the layers separately; do not add them into "
            "one accuracy."
        ),
        "labels": list(LABELS),
        "layers": {
            name: {
                "cases": count,
                "label_source": "human",
                "note": LAYER_NOTES[name],
            }
            for name, count in sorted(per_layer.items(), key=lambda item: LAYER_ORDER[item[0]])
        },
        "intent_notes": INTENT_NOTES,
        "label_rule": LABEL_RULE,
        "min_per_intent": MIN_PER_INTENT,
        "per_intent": dict(sorted(per_intent.items())),
        "sources": {
            "external_selection": (INTENT_DIR / "external_selection.jsonl").name,
            "external_manifest": EXTERNAL_MANIFEST_PATH.name,
            "external_manifest_sha256": _digest(EXTERNAL_MANIFEST_PATH),
            "authored": AUTHORED_PATH.name,
            "authored_sha256": _digest(AUTHORED_PATH),
            "gap_closures": CLOSURES_PATH.name,
            "gap_closures_sha256": _digest(CLOSURES_PATH),
        },
        "cases": cases,
    }


def render(dataset: dict[str, Any]) -> str:
    return json.dumps(dataset, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the intent evaluation dataset.")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    text = render(build())
    if args.check:
        if not DATASET_PATH.exists():
            print(f"INTENT SET FAIL missing {DATASET_PATH}", file=sys.stderr)
            return 1
        if DATASET_PATH.read_text(encoding="utf-8") != text:
            print(f"INTENT SET FAIL {DATASET_PATH} differs", file=sys.stderr)
            return 1
        print(f"INTENT SET OK cases={len(json.loads(text)['cases'])}")
        return 0

    if DATASET_PATH.exists() and not args.force:
        if DATASET_PATH.read_text(encoding="utf-8") != text:
            print("committed dataset differs; pass --force to replace it", file=sys.stderr)
            return 1
        print(f"ok: {DATASET_PATH} is current")
        return 0

    DATASET_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {DATASET_PATH} cases={len(json.loads(text)['cases'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
