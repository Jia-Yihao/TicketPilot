"""Sample user phrasings from a downloaded customer-service corpus.

The corpus is **not** vendored: it is a 7 MB external file, and the sample is
the only artifact that enters the repository. The sample keeps the wording and
the line number it came from; the corpus's own answers and intent labels are
never copied, and no sample is used as a labelled retrieval case.

Usage::

    python scripts/sample_external_phrasings.py --source .tmp/ext/ecom_dev.jsonl

The script is deterministic: it walks the JSONL in file order, keeps the first
200 unique user messages that mention a policy topic, and refuses to overwrite
an existing sample with different content unless ``--force`` is given.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "evals" / "external"
SAMPLE_PATH = OUT_DIR / "ecommerce_customer_service_queries.jsonl"
MANIFEST_PATH = OUT_DIR / "manifest.json"

DATASET = "modelscope_mp_677764216/E_commerce_Customer_Service"
DATASET_URL = (
    "https://www.modelscope.cn/datasets/"
    "modelscope_mp_677764216/E_commerce_Customer_Service"
)
SOURCE_FILE = "dev_clean_v2.jsonl"
LICENSE = "Apache-2.0"
SAMPLE_SIZE = 200

TOPIC_WORDS = (
    "退款",
    "退货",
    "退钱",
    "退费",
    "换货",
    "售后",
    "发货",
    "物流",
    "快递",
    "配送",
    "包裹",
    "到货",
    "签收",
    "延迟",
    "晚到",
    "运费",
    "邮费",
    "补发",
    "少发",
    "漏发",
    "破损",
    "包装",
    "拆封",
    "工单",
    "客服",
)
_LONG_DIGITS = re.compile(r"\d{8,}")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean(text: str) -> str | None:
    query = " ".join(text.split())
    if not 4 <= len(query) <= 40:
        return None
    if _LONG_DIGITS.search(query) or "http" in query.lower():
        return None
    if not any(word in query for word in TOPIC_WORDS):
        return None
    return query


def sample(source: Path) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    with source.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if len(picked) >= SAMPLE_SIZE:
                break
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for message in row.get("messages", []):
                if message.get("role") != "user":
                    continue
                query = _clean(str(message.get("content", "")))
                if query is None or query in seen:
                    continue
                seen.add(query)
                picked.append(
                    {
                        "query": query,
                        "provenance": {
                            "dataset": DATASET,
                            "file": SOURCE_FILE,
                            "line": line_number,
                        },
                    }
                )
                break
    return picked


def render(rows: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sample external customer-service phrasings."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing sample that differs from the fresh selection.",
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"missing source: {args.source}", file=sys.stderr)
        return 2
    rows = sample(args.source)
    if len(rows) < SAMPLE_SIZE:
        print(
            f"only {len(rows)} matching rows in {args.source}",
            file=sys.stderr,
        )
        return 2

    text = render(rows)
    if SAMPLE_PATH.exists() and not args.force:
        if SAMPLE_PATH.read_text(encoding="utf-8") != text:
            print(
                f"{SAMPLE_PATH} differs; pass --force to replace it",
                file=sys.stderr,
            )
            return 1
        print(f"ok: {SAMPLE_PATH} matches the selection")
    else:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        SAMPLE_PATH.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {SAMPLE_PATH}")

    manifest = {
        "dataset": DATASET,
        "dataset_url": DATASET_URL,
        "license": LICENSE,
        "source_file": SOURCE_FILE,
        "source_sha256": _digest(args.source),
        "source_rows": sum(1 for _ in args.source.open(encoding="utf-8")),
        "sample_file": SAMPLE_PATH.name,
        "sample_rows": len(rows),
        "sample_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "selection": (
            "first 200 unique user messages in file order, 4-40 characters, "
            "containing a policy topic word; no answer or label is copied"
        ),
        "used_for": "phrasing source only; not a labelled retrieval case",
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
