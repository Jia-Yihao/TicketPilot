"""Build ``evals/retrieval_set.json`` from the hand-written sources.

The generator is the only place the 1k set is assembled. Gold cases are
authored in ``evals/retrieval/gold_cases.json``; silver cases come from
``evals/retrieval/silver_templates.json`` (8 patterns x 5 slots per template).
Nothing is sampled from the policy text, and no external label is imported.

Run ``python scripts/build_retrieval_set.py`` to write the file and
``python scripts/build_retrieval_set.py --check`` to verify that the checked-in
file still equals a fresh generation.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app.retrieval.rewrite import FACET_ORDER, SECTION_TITLES

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "evals" / "retrieval"
GOLD_PATH = SOURCE_DIR / "gold_cases.json"
SILVER_PATH = SOURCE_DIR / "silver_templates.json"
THRESHOLDS_PATH = SOURCE_DIR / "thresholds.json"
OUT_PATH = ROOT / "evals" / "retrieval_set.json"
POLICY_DIR = ROOT / "app" / "resources" / "policies"

GOLD_TOTAL = 600
SILVER_TOTAL = 1600
K_VALUES = (1, 3, 5)


class DatasetError(ValueError):
    """A source file violates the dataset contract."""


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{path}: invalid JSON: {exc}") from exc


def _policy_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(POLICY_DIR.glob("*.md"))
    )


def _counts(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(cases),
        "by_split": dict(sorted(Counter(case["split"] for case in cases).items())),
        "by_policy": dict(
            sorted(Counter(case["policy_id"] for case in cases).items())
        ),
        "by_facet": dict(
            sorted(
                Counter(facet for case in cases for facet in case["facets"]).items()
            )
        ),
        "by_section": dict(
            sorted(Counter(case["sections"][0] for case in cases).items())
        ),
    }


def _gold_cases(source: dict[str, Any], policy_text: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for group in source["sections"]:
        policy_id = group["policy_id"]
        section = group["section"]
        slug = group["slug"]
        entries = group["cases"]
        if section not in SECTION_TITLES.get(policy_id, ()):
            raise DatasetError(
                f"{GOLD_PATH}: {policy_id} has no active section {section!r}"
            )
        half = len(entries) // 2
        for index, entry in enumerate(entries, start=1):
            query = entry["query"].strip()
            facet = entry["facet"]
            if facet not in FACET_ORDER:
                raise DatasetError(f"{GOLD_PATH}: unknown facet {facet!r}")
            if not 4 <= len(query) <= 60:
                raise DatasetError(f"{GOLD_PATH}: unusual query length: {query!r}")
            if query in policy_text:
                raise DatasetError(
                    f"{GOLD_PATH}: gold query copies the policy text: {query!r}"
                )
            cases.append(
                {
                    "id": f"g-{slug}-{index:02d}",
                    "query": query,
                    "policy_id": policy_id,
                    "sections": [section],
                    "facets": [facet],
                    "label_quality": "gold",
                    "source": "authored",
                    "split": "dev" if index <= half else "test",
                }
            )
    return cases


def _silver_cases(source: dict[str, Any]) -> list[dict[str, Any]]:
    slot_sets = source["slot_sets"]
    cases: list[dict[str, Any]] = []
    for template in source["templates"]:
        template_id = template["template_id"]
        policy_id = template["policy_id"]
        section = template["section"]
        facet = template["facet"]
        if section not in SECTION_TITLES.get(policy_id, ()):
            raise DatasetError(
                f"{SILVER_PATH}: {policy_id} has no active section {section!r}"
            )
        if facet not in FACET_ORDER:
            raise DatasetError(f"{SILVER_PATH}: unknown facet {facet!r}")
        try:
            slots = slot_sets[template["slots"]]
        except KeyError as exc:
            raise DatasetError(
                f"{SILVER_PATH}: {template_id} names an unknown slot set"
            ) from exc
        patterns = template["patterns"]
        rows = [(pattern, slot) for pattern in patterns for slot in slots]
        half = len(rows) // 2
        for index, (pattern, slot) in enumerate(rows):
            missing = {
                key
                for key in _placeholders(pattern)
                if key not in slot
            }
            if missing:
                raise DatasetError(
                    f"{SILVER_PATH}: {template_id} pattern needs {sorted(missing)}"
                )
            query = pattern.format(**slot).strip()
            cases.append(
                {
                    "id": f"s-{template_id}-{index:02d}",
                    "query": query,
                    "policy_id": policy_id,
                    "sections": [section],
                    "facets": [facet],
                    "label_quality": "silver",
                    "source": "template",
                    "split": "dev" if index < half else "test",
                    "template_id": template_id,
                }
            )
    return cases


def _placeholders(pattern: str) -> set[str]:
    found: set[str] = set()
    cursor = 0
    while True:
        start = pattern.find("{", cursor)
        if start < 0:
            return found
        end = pattern.find("}", start)
        if end < 0:
            raise DatasetError(f"unclosed placeholder in {pattern!r}")
        found.add(pattern[start + 1 : end])
        cursor = end + 1


def build() -> dict[str, Any]:
    gold = _gold_cases(_load(GOLD_PATH), _policy_text())
    silver = _silver_cases(_load(SILVER_PATH))
    if len(gold) != GOLD_TOTAL:
        raise DatasetError(f"gold cases: expected {GOLD_TOTAL}, got {len(gold)}")
    if len(silver) != SILVER_TOTAL:
        raise DatasetError(f"silver cases: expected {SILVER_TOTAL}, got {len(silver)}")

    cases = gold + silver
    ids = [case["id"] for case in cases]
    if len(set(ids)) != len(ids):
        raise DatasetError("duplicate case id")
    queries = [case["query"] for case in cases]
    duplicates = [query for query, count in Counter(queries).items() if count > 1]
    if duplicates:
        raise DatasetError(f"duplicate query: {duplicates[:3]}")

    return {
        "version": 2,
        "description": (
            "分层检索评测集：600 条手写金标（逐条对照政策原文）+ 1600 条模板银标"
            "（标签由构造决定），dev/test 各半，test 分片 1100 条。金银分开报告，"
            "银标不设门槛。生成器：scripts/build_retrieval_set.py。"
        ),
        "k_values": list(K_VALUES),
        "gold_counts": _counts(gold),
        "silver_counts": _counts(silver),
        # Only the legacy gold set and gold_test carry pass/fail floors; the
        # silver layers report coverage and are never judged against these.
        "thresholds": _load(THRESHOLDS_PATH),
        "cases": cases,
    }


def render(dataset: dict[str, Any]) -> str:
    return json.dumps(dataset, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or verify the layered retrieval evaluation set."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare a fresh generation with the checked-in file.",
    )
    args = parser.parse_args()

    try:
        text = render(build())
    except DatasetError as exc:
        print(f"dataset error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        if not OUT_PATH.exists():
            print(f"missing {OUT_PATH}", file=sys.stderr)
            return 1
        actual = OUT_PATH.read_text(encoding="utf-8")
        if actual != text:
            print(
                f"{OUT_PATH} is stale; run python scripts/build_retrieval_set.py",
                file=sys.stderr,
            )
            return 1
        print(f"ok: {OUT_PATH} matches the sources")
        return 0

    OUT_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
