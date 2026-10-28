"""Build the external-phrasing layer of the intent evaluation corpus.

The source corpus is **not** vendored: it is a 7 MB external file. What enters
the repository is one row per selected user message, with the line number it
came from and a human label written against ``app/agent/intents.yaml``. The
corpus's own answers and labels are never copied.

Selection is a file in the repository (``external_selection.jsonl``) holding
line number + label + a one-line note. The text is always re-read from the
source, so a stale selection cannot silently disagree with the corpus.

Usage::

    python scripts/sample_intent_phrasings.py --source .tmp/ext/ecom_dev.jsonl
    python scripts/sample_intent_phrasings.py --source .tmp/ext/ecom_dev.jsonl --check

``--check`` recomputes the sample and compares it byte for byte with the
committed files; it is the command CI can run when the source is present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "evals" / "intent"
SELECTION_PATH = OUT_DIR / "external_selection.jsonl"
SAMPLE_PATH = OUT_DIR / "external_phrasings.jsonl"
MANIFEST_PATH = OUT_DIR / "external_manifest.json"

DATASET = "modelscope_mp_677764216/E_commerce_Customer_Service"
DATASET_URL = (
    "https://www.modelscope.cn/datasets/"
    "modelscope_mp_677764216/E_commerce_Customer_Service"
)
LICENSE = "Apache-2.0"
SOURCE_FILE = "dev_clean_v2.jsonl"

LABELS = frozenset(
    {
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
    }
)

# De-identification is a refusal, not a rewrite: a message that looks like it
# carries an order id, a phone number, an email or a URL never enters the file.
_LONG_DIGITS = re.compile(r"\d{6,}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
MIN_CHARS = 4
MAX_CHARS = 60


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_selection(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_lines: set[int] = set()
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{number}: invalid JSON: {exc}") from exc
        for key in ("id", "line", "gold", "note"):
            if key not in row:
                raise SystemExit(f"{path}:{number}: missing {key!r}")
        if row["gold"] not in LABELS:
            raise SystemExit(f"{path}:{number}: unknown gold {row['gold']!r}")
        if row["id"] in seen_ids:
            raise SystemExit(f"{path}:{number}: duplicate id {row['id']!r}")
        if row["line"] in seen_lines:
            raise SystemExit(f"{path}:{number}: duplicate line {row['line']!r}")
        seen_ids.add(row["id"])
        seen_lines.add(int(row["line"]))
        rows.append(row)
    return rows


def _first_user_message(row: dict[str, Any]) -> str | None:
    for message in row.get("messages", []):
        if message.get("role") == "user":
            return str(message.get("content", "")).strip()
    return None


def _clean(text: str) -> str | None:
    query = " ".join(text.split())
    if not MIN_CHARS <= len(query) <= MAX_CHARS:
        return None
    if _LONG_DIGITS.search(query) or _EMAIL.search(query) or _URL.search(query):
        return None
    return query


def sample(source: Path, selection: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = {int(row["line"]): row for row in selection}
    order = {int(row["line"]): index for index, row in enumerate(selection)}
    picked: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    with source.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            chosen = wanted.get(line_number)
            if chosen is None:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{source}:{line_number}: invalid JSON: {exc}") from exc
            text = _first_user_message(row)
            if text is None:
                raise SystemExit(f"{source}:{line_number}: no user message")
            query = _clean(text)
            if query is None:
                raise SystemExit(
                    f"{source}:{line_number}: refused by de-identification "
                    f"or length rules: {text!r}"
                )
            if query in seen_text:
                raise SystemExit(f"{source}:{line_number}: duplicate text {query!r}")
            seen_text.add(query)
            picked.append(
                {
                    "id": chosen["id"],
                    "text": query,
                    "gold": chosen["gold"],
                    "layer": "external",
                    "label_source": "human",
                    "note": chosen["note"],
                    "provenance": {
                        "dataset": DATASET,
                        "file": SOURCE_FILE,
                        "line": line_number,
                    },
                    "_order": order[line_number],
                }
            )

    missing = sorted(set(wanted) - {row["provenance"]["line"] for row in picked})
    if missing:
        raise SystemExit(f"selection lines not found in source: {missing}")
    picked.sort(key=lambda row: row["_order"])
    for row in picked:
        row.pop("_order")
    return picked


def render(rows: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )


def manifest(
    source: Path, selection: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    counts = Counter(row["gold"] for row in rows)
    return {
        "dataset": DATASET,
        "dataset_url": DATASET_URL,
        "license": LICENSE,
        "source_file": SOURCE_FILE,
        "source_sha256": _digest(source),
        "source_rows": sum(1 for _ in source.open(encoding="utf-8")),
        "selection_file": SELECTION_PATH.name,
        "selection_rows": len(selection),
        "selection_sha256": _digest(SELECTION_PATH),
        "sample_file": SAMPLE_PATH.name,
        "sample_rows": len(rows),
        "sample_sha256": hashlib.sha256(render(rows).encode("utf-8")).hexdigest(),
        "per_intent": dict(sorted(counts.items())),
        "labels": "human, written against app/agent/intents.yaml; no corpus answer or label copied",
        "selection": (
            "line numbers listed in external_selection.jsonl; the first user "
            "message of each line, 4-60 characters, refused if it contains 6+ "
            "consecutive digits, an email or a URL"
        ),
        "used_for": "intent evaluation only; not a retrieval case",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sample labelled user phrasings for the intent corpus."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Recompute and compare with the committed files instead of writing.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace committed files that differ from the fresh sample.",
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"missing source: {args.source}", file=sys.stderr)
        return 2

    selection = _load_selection(SELECTION_PATH)
    rows = sample(args.source, selection)
    text = render(rows)
    meta = manifest(args.source, selection, rows)

    if args.check:
        failures: list[str] = []
        if not SAMPLE_PATH.exists():
            failures.append(f"missing {SAMPLE_PATH}")
        elif SAMPLE_PATH.read_text(encoding="utf-8") != text:
            failures.append(f"{SAMPLE_PATH} does not match the selection")
        if not MANIFEST_PATH.exists():
            failures.append(f"missing {MANIFEST_PATH}")
        else:
            committed = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            for key, value in meta.items():
                if committed.get(key) != value:
                    failures.append(f"{MANIFEST_PATH}: {key} differs")
        for item in failures:
            print(f"EXTERNAL FAIL {item}", file=sys.stderr)
        if failures:
            return 1
        print(f"EXTERNAL OK rows={len(rows)}")
        return 0

    if (SAMPLE_PATH.exists() or MANIFEST_PATH.exists()) and not args.force:
        fresh = text
        current = (
            SAMPLE_PATH.read_text(encoding="utf-8") if SAMPLE_PATH.exists() else None
        )
        if current != fresh or not MANIFEST_PATH.exists():
            print("committed files differ; pass --force to replace them", file=sys.stderr)
            return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SAMPLE_PATH.write_text(text, encoding="utf-8", newline="\n")
    MANIFEST_PATH.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {SAMPLE_PATH} rows={len(rows)}")
    print(f"wrote {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
