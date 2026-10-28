"""只读等价性比对：写意图判定的「过程层」差异清单 + 「端到端」判定序列。

阶段 B（F-9）的门禁口径见 plan.md §4.0：

* 端到端：346 条语料上 ``classify_support()`` 输出的 ``SupportPlan.intent`` 序列，
  这是用户可见的判定。
* 过程：``classify()`` 的写意图分支 vs ``catalog`` 的 ``write_path: true`` 行。
  本轮真正改动的对象只有这一层。

本脚本**不判定、不改文件、不连模型、不读时钟**（除记录生成时刻）。它只做三件事：

1. 对每条语料同时算出「classify 的写意图读数」与「catalog 写行的读数」；
2. 对每条语料算出端到端读数 ``classify_support()``；
3. 输出 JSON，由 ``--diff`` 决定与哪份基线比、把差异打成清单。

用法（仓库根）::

    .venv/Scripts/python.exe openspec/changes/add-intent-routing-data-driven-2026-10/compare_write_intents.py \\
        --out .tmp/equiv_pre.json
    .venv/Scripts/python.exe openspec/changes/add-intent-routing-data-driven-2026-10/compare_write_intents.py \\
        --out .tmp/equiv_step1.json --diff .tmp/equiv_pre.json

退出码：0=无差异；1=端到端序列或写意图基线出现漂移；2=过程层写意图差异非空（仅报告，不判失败）。
过程层差异在步骤 1 预期非空/为空取决于现状，因此只影响退出码 2 的提示，不影响门禁结论。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.agent.intent_catalog import IntentCatalog, _load, get_catalog  # noqa: E402
from app.agent.routing import (  # noqa: E402
    blocks_write_action,
    classify,
    extract_amount,
    extract_order_id,
)
from app.agent.support_agent import classify_support  # noqa: E402
from app.models.enums import Intent  # noqa: E402
from app.services.catalog import CatalogService  # noqa: E402

DATASET_PATH = ROOT / "evals" / "intent" / "intent_set.json"


def _slot_context(text: str) -> dict[str, Any]:
    context: dict[str, Any] = {}
    order_id = extract_order_id(text)
    if order_id:
        context["order_id"] = order_id
    amount_cents, _invalid = extract_amount(text)
    if amount_cents is not None:
        context["amount_cents"] = amount_cents
    return context


def _write_ids(catalog: IntentCatalog) -> tuple[str, ...]:
    """目录里 write_path: true 的行，按 priority 升序（与 ordered_specs 同序）。"""
    return tuple(spec.intent_id for spec in catalog.ordered_specs() if spec.write_path)


def _catalog_write_verdict(text: str, catalog: IntentCatalog) -> str | None:
    """模拟切换后的判据：遍历 write_path 行，先过 match_request_only。

    只认 ``Intent`` 枚举里存在的行（白名单），语义与 plan §2.1 一致：
    不在可认领集合里的行跳过，DELIVERY_ISSUE 显式豁免。
    """
    claimed = {member.value for member in Intent}
    blocked = blocks_write_action(text)
    for spec in catalog.ordered_specs():
        if not getattr(spec, "write_path", False):
            continue
        if spec.intent_id not in claimed:
            continue
        if spec.match_request_only and blocked:
            continue
        if any(cue in text for cue in spec.cues):
            return spec.intent_id
    return None


def _classify_write_verdict(route_intent: Intent) -> str | None:
    if route_intent in (Intent.REFUND_REQUEST, Intent.TICKET_CREATE):
        return route_intent.value
    return None


def measure(catalog: IntentCatalog) -> dict[str, Any]:
    """对 346 条语料逐条算出三层读数；本函数不判定、不写文件。

    上下文与 ``has_catalog_match`` 的取法与 ``evals/intent_metrics.py::_read_rule``
    同形，因此这份端到端基线与 A1 的对照物读的是同一句话。
    """
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    products = CatalogService()
    rows = []
    for case in cases:
        text = str(case["text"])
        lowered = text.lower()
        route = classify(text, _slot_context(text))
        message_order_id = extract_order_id(text)
        matched = products.resolve(text)
        spec = catalog.match(
            lowered,
            has_catalog_match=matched is not None,
            has_order_id=bool(message_order_id),
        )
        plan = classify_support(
            text,
            route,
            has_catalog_match=matched is not None,
            message_order_id=message_order_id,
            order_id=route.order_id,
        )
        rows.append(
            {
                "id": case["id"],
                "layer": case.get("layer"),
                "gold": case.get("gold"),
                "text": text,
                "classify_intent": route.intent.value,
                "classify_write": _classify_write_verdict(route.intent),
                "catalog_write": _catalog_write_verdict(lowered, catalog),
                "catalog_match": spec.intent_id if spec is not None else None,
                "catalog_nature": spec.nature if spec is not None else None,
                "support_intent": plan.intent.value,
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus": str(DATASET_PATH.relative_to(ROOT)),
        "case_count": len(rows),
        "write_path_rows": _write_ids(catalog),
        "rows": rows,
    }


def _index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in payload["rows"]}


# 「硬」字段是判定本身：漂一条就是行为变了，门禁必须红。
# 「软」字段是 catalog 的读数：本轮的目的就是让它接管写意图判定，因此它的变化
# 是施工面，逐条进差异清单留档，但不算失败。
_HARD_FIELDS = (
    ("support_intent", "端到端 SupportPlan.intent"),
    ("classify_intent", "classify().intent"),
    ("classify_write", "classify 写意图读数"),
)
_SOFT_FIELDS = (
    ("catalog_write", "catalog 写行读数"),
    ("catalog_match", "catalog.match() 读数"),
)


def diff_against(
    base: dict[str, Any], now: dict[str, Any]
) -> tuple[list[str], list[dict[str, Any]]]:
    old = _index(base)
    hard: list[str] = []
    changed: list[dict[str, Any]] = []
    for row in now["rows"]:
        before = old.get(row["id"])
        if before is None:
            hard.append(f"- {row['id']}：基线里没有这条语料（语料变了，本轮不该动它）")
            continue
        for field, label in _HARD_FIELDS:
            if before[field] != row[field]:
                hard.append(
                    f"- {row['id']} 「{row['text']}」{label}："
                    f"{before[field]} → {row[field]}"
                )
        moved = {
            label: (before[field], row[field])
            for field, label in _SOFT_FIELDS
            if before[field] != row[field]
        }
        if moved:
            changed.append(
                {
                    "id": row["id"],
                    "text": row["text"],
                    "classify": row["classify_intent"],
                    "classify_write": row["classify_write"],
                    "support": row["support_intent"],
                    "moved": moved,
                }
            )
    return hard, changed


def process_write_diffs(now: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in now["rows"]
        if row["classify_write"] != row["catalog_write"]
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="写出本次读数的 JSON 路径")
    parser.add_argument("--diff", help="与这份基线比对，打印漂移清单")
    parser.add_argument(
        "--catalog",
        default=None,
        help="用这份 intents.yaml 构造目录（默认读生产文件）；影子模式比对用",
    )
    args = parser.parse_args()

    catalog = _load(Path(args.catalog)) if args.catalog else get_catalog()
    payload = measure(catalog)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    write_diffs = process_write_diffs(payload)
    print(f"cases={payload['case_count']} write_path_rows={payload['write_path_rows']}")
    print(f"过程层写意图差异={len(write_diffs)}")
    for row in write_diffs:
        print(
            f"  - {row['id']} 「{row['text']}」 classify={row['classify_write']}"
            f" catalog={row['catalog_write']} support={row['support_intent']}"
        )

    code = 0
    if args.diff:
        base = json.loads(Path(args.diff).read_text(encoding="utf-8"))
        hard, changed = diff_against(base, payload)
        print(f"判定漂移(硬)={len(hard)}  catalog 读数变化(留档)={len(changed)}")
        for line in hard:
            print(f"  {line}")
        for item in changed:
            moves = "; ".join(
                f"{label} {was} → {now}" for label, (was, now) in item["moved"].items()
            )
            print(f"  - {item['id']} 「{item['text']}」 {moves}")
        if hard:
            code = 1

    read_divergence = [
        row
        for row in payload["rows"]
        if row["classify_intent"] != row["catalog_match"]
    ]
    print(f"读路径既有分歧(classify.intent != catalog.match)={len(read_divergence)}")
    groups: dict[tuple[str, str], int] = {}
    for row in read_divergence:
        key = (str(row["classify_intent"]), str(row["catalog_match"]))
        groups[key] = groups.get(key, 0) + 1
    for (left, right), count in sorted(groups.items(), key=lambda item: -item[1]):
        print(f"  - classify={left} catalog={right} 条数={count}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
