"""A replayable snapshot of what the model returns, not just one lucky answer.

``scripts/llm_gap_probe.py`` answers "can the model read these sentences at
all?" with one call per sentence. That is a demonstration, not a distribution:
a single sample cannot say how stable the reading is, and an exact amount is
the wrong assertion for a model that may round "一百二十九元" to 12900 or 13000.

This script replays a labelled set ``--repeat`` times and records:

* the raw model intent and the adopted intent (rules first, as in
  ``IntentBrain.plan``); the intent-change ratio has two denominators — all
  turns, and the turns the rules could not read;
* amount fills against a predefined range per case (``within`` / ``outside``
  / ``missing``), never against one exact integer;
* order-id recovery and fabrication (an id not present in the text);
* confidence and validation-retry counts, plus model failures.

``--save`` writes the snapshot; ``--compare`` reads an older one and reports
the delta in totals and the per-case distribution distance. The labels and
ranges are the author's, not a tenant's ground truth — the same boundary as
the rest of the model-gap evidence.

    OPENAI_BASE_URL=http://127.0.0.1:1234/v1 OPENAI_API_KEY=lmstudio \\
      python evals/model_distribution.py --repeat 3 --save .tmp/model-dist.json

    python evals/model_distribution.py --repeat 3 --compare .tmp/model-dist.json
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.agent.pydantic_agent import IntentOutput, ModelTrace
from app.agent.routing import classify
from app.models.enums import Intent


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    text: str
    expected_intent: str
    expected_order_id: str | None = None
    amount_range: tuple[int, int] | None = None
    control: bool = False


# Hand-written phrasings, same boundary as scripts/llm_gap_probe.py: the rule
# table reads most of them as UNKNOWN, and the labels are a human reading.
# ``control`` cases are already readable by the rules and must not be changed
# by a disagreeing model.
CASES: tuple[Case, ...] = (
    Case("refund-order", "ORD-DEMO-1001 那笔钱给我退回来", "REFUND_REQUEST", "ORD-DEMO-1001"),
    Case("refund-broken", "我上周买的那个杯子收到就碎了，这单我不要了，把钱还我", "REFUND_REQUEST"),
    Case("refund-partial", "我申请退一部分款", "REFUND_REQUEST"),
    Case("refund-useless", "东西根本没法用，我不想留了，把付的钱给我回来", "REFUND_REQUEST"),
    Case(
        "refund-amount-chinese",
        "订单 ORD-DEMO-1001，金额一百二十九元，我要申请退款",
        "REFUND_REQUEST",
        "ORD-DEMO-1001",
        (12_000, 14_000),
    ),
    Case(
        "refund-order-amount",
        "ORD-DEMO-1001 退一百二十九元",
        "REFUND_REQUEST",
        "ORD-DEMO-1001",
        (12_000, 14_000),
    ),
    Case("handoff-human", "别跟我绕了，叫个活人来", "TICKET_CREATE"),
    Case("handoff-manager", "我要找你们负责人，机器人在糊弄我", "TICKET_CREATE"),
    Case("handoff-third", "这事儿我忍了三次了，给我找个真人", "TICKET_CREATE"),
    Case("question-shipping", "运费谁出？", "POLICY_QA"),
    Case("question-arrival", "我买的东西还没到，能等到几号", "LOGISTICS_QUERY"),
    Case(
        "control-refund",
        "我要申请退款，订单 ORD-DEMO-1001，金额 129 元",
        "REFUND_REQUEST",
        "ORD-DEMO-1001",
        (12_900, 12_900),
        control=True,
    ),
    Case("control-policy", "退款政策规定哪些订单可以退款", "POLICY_QA", control=True),
)


class Extractor(Protocol):
    def parse(
        self, request: str, context: dict[str, Any] | None = None
    ) -> tuple[IntentOutput, ModelTrace]: ...


@dataclass(frozen=True, slots=True)
class Reading:
    raw_intent: str
    adopted_intent: str
    raw_amount: int | None
    adopted_amount: int | None
    raw_order_id: str | None
    fabricated_order_id: str | None
    confidence: float
    status: str
    retries: int


def adopt(rules_plan: Any, output: IntentOutput) -> tuple[str, int | None, str | None]:
    """Mirror ``IntentBrain.plan``'s adoption rule for one output.

    Rules win wherever they read something; the model fills an ``UNKNOWN``
    intent and a missing amount. Keeping this in the evaluation makes the
    distribution measurable without paying for a second model call.
    """
    intent = rules_plan.intent.value
    if rules_plan.intent is Intent.UNKNOWN and output.intent != "UNKNOWN":
        intent = output.intent
    amount = (
        rules_plan.amount_cents
        if rules_plan.amount_cents is not None
        else output.amount_cents
    )
    order_id = rules_plan.order_id or output.order_id
    return intent, amount, order_id


def read_case(case: Case, extractor: Extractor) -> Reading:
    rules_plan = classify(case.text, {})
    try:
        output, trace = extractor.parse(case.text, {})
    except Exception as exc:
        return Reading(
            raw_intent="FAILED",
            adopted_intent=rules_plan.intent.value,
            raw_amount=None,
            adopted_amount=rules_plan.amount_cents,
            raw_order_id=None,
            fabricated_order_id=None,
            confidence=0.0,
            status=getattr(getattr(exc, "trace", None), "status", "FAILED"),
            retries=getattr(getattr(exc, "trace", None), "retries", 0),
        )
    adopted_intent, adopted_amount, _ = adopt(rules_plan, output)
    fabricated = (
        output.order_id
        if output.order_id is not None
        and output.order_id.lower() not in case.text.lower()
        else None
    )
    return Reading(
        raw_intent=output.intent,
        adopted_intent=adopted_intent,
        raw_amount=output.amount_cents,
        adopted_amount=adopted_amount,
        raw_order_id=output.order_id,
        fabricated_order_id=fabricated,
        confidence=output.confidence,
        status=trace.status,
        retries=trace.retries,
    )


def run(
    cases: tuple[Case, ...], extractor: Extractor, *, repeat: int, model: str
) -> dict[str, Any]:
    per_case: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    confidence: list[float] = []
    for case in cases:
        rules_plan = classify(case.text, {})
        raw_intents: Counter[str] = Counter()
        adopted_intents: Counter[str] = Counter()
        amounts: Counter[str] = Counter()
        statuses: Counter[str] = Counter()
        case_confidence: list[float] = []
        fabricated = 0
        order_recovered = 0
        for _ in range(repeat):
            reading = read_case(case, extractor)
            totals["turns"] += 1
            raw_intents[reading.raw_intent] += 1
            adopted_intents[reading.adopted_intent] += 1
            statuses[reading.status] += 1
            case_confidence.append(reading.confidence)
            confidence.append(reading.confidence)
            if rules_plan.intent is Intent.UNKNOWN:
                totals["rules_unknown_turns"] += 1
                if reading.adopted_intent != rules_plan.intent.value:
                    totals["adopted_changes"] += 1
            if reading.fabricated_order_id:
                fabricated += 1
                totals["order_fabricated"] += 1
            if (
                case.expected_order_id
                and reading.raw_order_id == case.expected_order_id
            ):
                order_recovered += 1
                totals["order_recovered"] += 1
            if case.expected_order_id:
                totals["order_expected_turns"] += 1
            if case.amount_range is not None:
                totals["amount_turns"] += 1
                amount = reading.adopted_amount
                low, high = case.amount_range
                if amount is None:
                    amounts["missing"] += 1
                elif low <= amount <= high:
                    amounts["within"] += 1
                    totals["amount_within"] += 1
                else:
                    amounts["outside"] += 1
                    totals["amount_outside"] += 1
            if reading.retries:
                totals["retries"] += reading.retries
            if reading.status != "OK":
                totals["model_failures"] += 1
            if reading.raw_intent == case.expected_intent:
                totals["raw_label_matches"] += 1
        per_case.append(
            {
                "id": case.id,
                "text": case.text,
                "control": case.control,
                "expected_intent": case.expected_intent,
                "rules_intent": rules_plan.intent.value,
                "amount_range": list(case.amount_range) if case.amount_range else None,
                "raw_intents": dict(raw_intents),
                "adopted_intents": dict(adopted_intents),
                "amounts": dict(amounts),
                "statuses": dict(statuses),
                "order_recovered": order_recovered,
                "order_fabricated": fabricated,
                "confidence": {
                    "mean": round(sum(case_confidence) / len(case_confidence), 4)
                    if case_confidence
                    else 0.0,
                    "min": round(min(case_confidence), 4) if case_confidence else 0.0,
                    "max": round(max(case_confidence), 4) if case_confidence else 0.0,
                },
            }
        )
    turns = totals["turns"] or 1
    unknown = totals["rules_unknown_turns"] or 1
    amount_turns = totals["amount_turns"] or 1
    order_turns = totals["order_expected_turns"] or 1
    result: dict[str, Any] = {
        "model": model,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "repeat": repeat,
        "totals": {
            "turns": totals["turns"],
            "model_failures": totals["model_failures"],
            "retries": totals["retries"],
            "rules_unknown_turns": totals["rules_unknown_turns"],
            "adopted_changes": totals["adopted_changes"],
            "adoption_change_rate": round(totals["adopted_changes"] / unknown, 4),
            "overall_intent_change_rate": round(totals["adopted_changes"] / turns, 4),
            "raw_label_matches": totals["raw_label_matches"],
            "raw_label_accuracy": round(totals["raw_label_matches"] / turns, 4),
            "amount_turns": totals["amount_turns"],
            "amount_within": totals["amount_within"],
            "amount_outside": totals["amount_outside"],
            "amount_accuracy": round(totals["amount_within"] / amount_turns, 4),
            "order_expected_turns": totals["order_expected_turns"],
            "order_recovered": totals["order_recovered"],
            "order_fabricated": totals["order_fabricated"],
            "order_fabrication_rate": round(
                totals["order_fabricated"] / order_turns, 4
            ),
            "mean_confidence": round(sum(confidence) / len(confidence), 4)
            if confidence
            else 0.0,
        },
        "cases": per_case,
    }
    return result


def compare(
    current: dict[str, Any], baseline: dict[str, Any]
) -> list[str]:
    """Delta in totals plus per-case distribution distance, as text lines."""
    lines = ["snapshot comparison (current vs baseline):"]
    old_totals = baseline.get("totals", {})
    for key, value in current["totals"].items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        old = old_totals.get(key)
        if old is None:
            continue
        delta = value - old
        marker = "" if delta == 0 else f"  ({delta:+.4f})"
        lines.append(f"  {key:<28} {old} -> {value}{marker}")

    old_cases = {case["id"]: case for case in baseline.get("cases", [])}
    distances: list[tuple[float, str]] = []
    for case in current["cases"]:
        old = old_cases.get(case["id"])
        if old is None:
            continue
        distance = _tv_distance(
            case.get("adopted_intents", {}), old.get("adopted_intents", {})
        )
        distances.append((distance, case["id"]))
    distances.sort(reverse=True)
    lines.append("  per-case adopted-intent distance (top 5):")
    for distance, case_id in distances[:5]:
        lines.append(f"    {case_id:<28} {distance:.3f}")
    return lines


def _tv_distance(left: dict[str, int], right: dict[str, int]) -> float:
    total_left = sum(left.values()) or 1
    total_right = sum(right.values()) or 1
    keys = set(left) | set(right)
    return 0.5 * sum(
        abs(left.get(key, 0) / total_left - right.get(key, 0) / total_right)
        for key in keys
    )


def _print_report(result: dict[str, Any]) -> None:
    totals = result["totals"]
    print(f"model: {result['model']}  repeat: {result['repeat']}  turns: {totals['turns']}")
    print("")
    print(
        "case                     rules            raw distribution          adopted          "
        "amount             conf"
    )
    print("-" * 116)
    for case in result["cases"]:
        raw = ", ".join(f"{key}:{value}" for key, value in sorted(case["raw_intents"].items()))
        adopted = ", ".join(
            f"{key}:{value}" for key, value in sorted(case["adopted_intents"].items())
        )
        amounts = ", ".join(f"{key}:{value}" for key, value in sorted(case["amounts"].items()))
        print(
            f"{case['id']:<24} {case['rules_intent']:<15} {raw:<25} {adopted:<16} "
            f"{amounts or '-':<18} {case['confidence']['mean']:.2f}"
        )
    print("")
    print(
        f"intent changed: {totals['adopted_changes']} / "
        f"{totals['rules_unknown_turns']} rules-UNKNOWN turns "
        f"({totals['adoption_change_rate']:.1%}); overall "
        f"{totals['overall_intent_change_rate']:.1%}"
    )
    print(
        f"amount within range: {totals['amount_within']}/{totals['amount_turns']} "
        f"({totals['amount_accuracy']:.1%}); outside "
        f"{totals['amount_outside']}; missing "
        f"{totals['amount_turns'] - totals['amount_within'] - totals['amount_outside']}"
    )
    print(
        f"order ids: recovered {totals['order_recovered']}/"
        f"{totals['order_expected_turns']}, fabricated {totals['order_fabricated']} "
        f"({totals['order_fabrication_rate']:.1%})"
    )
    print(
        f"raw label accuracy {totals['raw_label_accuracy']:.1%}; retries "
        f"{totals['retries']}; failures {totals['model_failures']}; mean confidence "
        f"{totals['mean_confidence']:.3f}"
    )
    print(
        "口径：标签与金额区间是我按人读法预设的；分布来自重复回放，"
        "不是真实工单占比，也不是托管模型读数。"
    )


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model",
        default="openai:qwen/qwen3-8b",
        help="pydantic-ai model string; OpenAI-compatible servers use OPENAI_BASE_URL.",
    )
    parser.add_argument("--repeat", type=int, default=3, help="Replays per sentence.")
    parser.add_argument("--save", type=Path, help="Write the snapshot JSON here.")
    parser.add_argument("--compare", type=Path, help="Compare against this snapshot.")
    parser.add_argument("--json", action="store_true", help="Print the snapshot as JSON.")
    args = parser.parse_args()

    if args.repeat < 1:
        parser.error("--repeat must be at least 1")

    from app.agent.pydantic_agent import IntentAgent

    result = run(
        CASES,
        IntentAgent(model_name=args.model),
        repeat=args.repeat,
        model=args.model,
    )

    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_report(result)
        if args.compare:
            baseline = json.loads(args.compare.read_text(encoding="utf-8"))
            print("")
            for line in compare(result, baseline):
                print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
