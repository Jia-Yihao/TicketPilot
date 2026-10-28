"""Offline intent evaluation on a held-out, hand-labelled corpus.

No network and no model by default. The rule readings come from the same
public entry point the chat turn uses — ``classify`` then
``classify_support`` — so the numbers measure the shipped classifier rather
than a copy of its cue table.

What the report separates, and why:

* **Layers.** ``external`` is real user phrasing from a public corpus;
  ``authored_gap`` fills the intents that corpus does not contain. They are
  reported side by side and never added into one accuracy: the second layer
  is coverage evidence, not a frequency estimate.
* **UNKNOWN vs wrong.** ``UNKNOWN`` is a legal answer. A case the rules
  decline is counted under ``unknown`` (``false_unknown`` at layer level),
  not under ``wrong``. Only a different *known* intent is a misread.
* **Cost.** Every case lands in exactly one of four buckets: ``correct``,
  ``write_intent_error``, ``read_intent_error``, ``false_unknown``. Costs are
  derived from the predicted intent and the catalog's required slots:
  missing slots become clarifications, a refund/ticket task that has all its
  slots becomes a task (and a refund task is at most one approval), a
  handoff prediction is one handoff, and a refund amount is *exposed* — not
  paid.
* **Model.** ``--model`` asks the model only on the rule-``UNKNOWN`` subset
  that its six-value label space can express. Fine intents outside that
  space are skipped and counted as ``out_of_label_space``; they are not
  model errors. Model failures are counted separately from wrong answers.

Usage::

    python evals/intent_metrics.py
    python evals/intent_metrics.py --enforce
    python evals/intent_metrics.py --json .tmp/intent.json
    OPENAI_BASE_URL=http://127.0.0.1:1234/v1 OPENAI_API_KEY=lmstudio \\
      python evals/intent_metrics.py --model --model-name openai:qwen/qwen3-8b

The ``--model`` run is the only path that touches a network endpoint.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.agent.intent_catalog import get_catalog
from app.agent.routing import (
    RoutePlan,
    classify,
    extract_amount,
    extract_order_id,
)
from app.agent.support_agent import classify_support
from app.services.catalog import CatalogService

DATASET_PATH = Path(__file__).with_name("intent") / "intent_set.json"

# The six labels the model contract can emit (``IntentLiteral``).
MODEL_LABELS = (
    "POLICY_QA",
    "ORDER_QUERY",
    "LOGISTICS_QUERY",
    "TICKET_CREATE",
    "REFUND_REQUEST",
    "UNKNOWN",
)
MODEL_LABEL_SET = frozenset(MODEL_LABELS)

# Intents whose first action creates a task. DELIVERY_ISSUE is ``info_action``:
# its first action is a read-only delivery review, so a wrong delivery reading
# costs a wrong answer, not a task. Both write intents also create a task.
WRITE_INTENTS = frozenset({"REFUND_REQUEST", "TICKET_CREATE"})

COST_BUCKETS = (
    "correct",
    "write_intent_error",
    "read_intent_error",
    "false_unknown",
)

# A write error is not one cost: inventing a write path is the expensive
# direction, missing a real one is a lost action, and confusing two write
# intents still opens a task but the wrong kind.
WRITE_SPLITS = ("extra_write", "missed_write", "write_to_write")

DEFAULT_MODEL = "openai:qwen/qwen3-8b"


@dataclass(frozen=True, slots=True)
class ModelReading:
    """One model answer, reduced to what the report measures."""

    intent: str
    order_id: str | None = None
    amount_cents: int | None = None
    status: str = "OK"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


ModelRunner = Callable[[str, dict[str, Any] | None], ModelReading]


@dataclass(frozen=True, slots=True)
class CaseReading:
    case_id: str
    layer: str
    gold: str
    text: str
    predicted: str
    bucket: str
    clarifications: int
    tasks: int
    approvals: int
    handoffs: int
    amount_exposed_cents: int
    order_id: str | None
    amount_cents: int | None
    rule_latency_ms: float
    model: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "layer": self.layer,
            "gold": self.gold,
            "text": self.text,
            "predicted": self.predicted,
            "bucket": self.bucket,
            "cost": {
                "clarifications": self.clarifications,
                "tasks": self.tasks,
                "approvals": self.approvals,
                "handoffs": self.handoffs,
                "amount_exposed_cents": self.amount_exposed_cents,
            },
            "slots": {
                "order_id": self.order_id,
                "amount_cents": self.amount_cents,
            },
            "rule_latency_ms": round(self.rule_latency_ms, 3),
            "model": self.model,
        }


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _slot_context(text: str) -> dict[str, Any]:
    """The context the chat turn would hand to routing for this message."""
    context: dict[str, Any] = {}
    order_id = extract_order_id(text)
    amount_cents, _ = extract_amount(text)
    if order_id:
        context["order_id"] = order_id
    if amount_cents is not None:
        context["amount_cents"] = amount_cents
    return context


def _read_rule(
    text: str, catalog: CatalogService
) -> tuple[RoutePlan, str, float]:
    """Classify one message exactly as ``SupportAgent.handle`` does.

    Only public entry points are used. The context is built from the message
    itself because this evaluation has no previous turn to inherit from.
    """
    started = time.perf_counter()
    context = _slot_context(text)
    route = classify(text, context)
    matched = catalog.resolve(text)
    plan = classify_support(
        text,
        route,
        has_catalog_match=matched is not None,
        message_order_id=extract_order_id(text),
        order_id=route.order_id,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    return route, plan.intent.value, elapsed_ms


def _bucket(gold: str, predicted: str) -> str:
    """Put one case in exactly one bucket, most expensive reading first.

    A known intent is only a ``read_intent_error`` when the write path is not
    involved in either direction. ``false_unknown`` wins over the write
    buckets because a declined message creates neither a task nor an answer.
    """
    if predicted == gold:
        return "correct"
    if predicted == "UNKNOWN":
        return "false_unknown"
    if predicted in WRITE_INTENTS or gold in WRITE_INTENTS:
        return "write_intent_error"
    return "read_intent_error"


def _write_split(gold: str, predicted: str) -> str | None:
    """Which direction a write-intent error went, or None for other buckets.

    ``extra_write`` — the user asked to read something and got a task.
    ``missed_write`` — the user asked to move money/open a case and got an
    answer instead. ``write_to_write`` — refund and ticket crossed.
    """
    if predicted == gold:
        return None
    gold_write = gold in WRITE_INTENTS
    predicted_write = predicted in WRITE_INTENTS
    if not gold_write and not predicted_write:
        return None
    if predicted_write and not gold_write:
        return "extra_write"
    if gold_write and not predicted_write:
        return "missed_write"
    return "write_to_write"


def _false_unknown_write(gold: str, predicted: str) -> bool:
    """An answerable write request that the rules declined to route."""
    return predicted == "UNKNOWN" and gold in WRITE_INTENTS


def _derived_cost(
    predicted: str, text: str
) -> tuple[int, int, int, int, int]:
    """Clarifications, tasks, approvals, handoffs, exposed cents.

    The catalog is the only input besides the message. ``approvals`` is the
    upper bound for a refund task: the task still has to pass eligibility
    before an approval row exists, so this is "at most one", never "paid".
    """
    catalog = get_catalog()
    present: set[str] = set()
    order_id = extract_order_id(text)
    amount_cents, _ = extract_amount(text)
    if order_id:
        present.add("order_id")
    if amount_cents is not None:
        present.add("amount_cents")

    missing = catalog.missing_slots(predicted, present)
    clarifications = len(missing)
    tasks = 1 if predicted in WRITE_INTENTS and not missing else 0
    approvals = 1 if predicted == "REFUND_REQUEST" and tasks else 0
    handoffs = 1 if predicted == "HUMAN_HANDOFF" else 0
    exposed = (
        amount_cents
        if predicted == "REFUND_REQUEST" and amount_cents is not None
        else 0
    )
    return clarifications, tasks, approvals, handoffs, exposed


def _read_case(case: dict[str, Any], catalog: CatalogService) -> CaseReading:
    text = str(case["text"])
    gold = str(case["gold"])
    _, predicted, latency = _read_rule(text, catalog)
    clarifications, tasks, approvals, handoffs, exposed = _derived_cost(
        predicted, text
    )
    amount_cents, _ = extract_amount(text)
    return CaseReading(
        case_id=str(case["id"]),
        layer=str(case["layer"]),
        gold=gold,
        text=text,
        predicted=predicted,
        bucket=_bucket(gold, predicted),
        clarifications=clarifications,
        tasks=tasks,
        approvals=approvals,
        handoffs=handoffs,
        amount_exposed_cents=exposed,
        order_id=extract_order_id(text),
        amount_cents=amount_cents,
        rule_latency_ms=latency,
    )


def _boundary() -> dict[str, Any]:
    return {
        "cases": 0,
        "share": None,
        "clarifications": 0,
        "tasks": 0,
        "approvals": 0,
        "handoffs": 0,
        "amount_exposed_cents": 0,
        # The rule path calls no model, so token cost is structurally zero.
        "tokens": 0,
        "rule_latency_ms": 0.0,
    }


def _cost_matrix(readings: Sequence[CaseReading], total: int) -> dict[str, Any]:
    matrix = {bucket: _boundary() for bucket in COST_BUCKETS}
    write_splits = Counter(
        split
        for row in readings
        if row.bucket == "write_intent_error"
        and (split := _write_split(row.gold, row.predicted)) is not None
    )
    for row in readings:
        bucket = matrix[row.bucket]
        bucket["cases"] += 1
        bucket["clarifications"] += row.clarifications
        bucket["tasks"] += row.tasks
        bucket["approvals"] += row.approvals
        bucket["handoffs"] += row.handoffs
        bucket["amount_exposed_cents"] += row.amount_exposed_cents
        bucket["rule_latency_ms"] += row.rule_latency_ms
    for bucket in matrix.values():
        bucket["share"] = _rate(bucket["cases"], total)
        bucket["rule_latency_ms"] = round(bucket["rule_latency_ms"], 3)
    matrix["write_intent_error"]["breakdown"] = {
        name: write_splits.get(name, 0) for name in WRITE_SPLITS
    }
    return matrix


def _layer_report(name: str, readings: Sequence[CaseReading]) -> dict[str, Any]:
    total = len(readings)
    correct = sum(1 for row in readings if row.predicted == row.gold)
    gold_unknown = sum(1 for row in readings if row.gold == "UNKNOWN")
    unknown_predictions = sum(1 for row in readings if row.predicted == "UNKNOWN")
    unknown_matched = sum(
        1
        for row in readings
        if row.gold == "UNKNOWN" and row.predicted == "UNKNOWN"
    )
    unknown_misread = sum(
        1
        for row in readings
        if row.gold == "UNKNOWN" and row.predicted != "UNKNOWN"
    )
    false_unknown = sum(
        1
        for row in readings
        if row.gold != "UNKNOWN" and row.predicted == "UNKNOWN"
    )
    false_unknown_write = sum(
        1 for row in readings if _false_unknown_write(row.gold, row.predicted)
    )
    wrong = total - correct - false_unknown
    answerable = total - gold_unknown
    correct_answerable = correct - unknown_matched

    per_intent: dict[str, Any] = {}
    labels = ["HUMAN_HANDOFF", "REFUND_REQUEST", "TICKET_CREATE", "REFUND_STATUS",
              "REFUND_ELIGIBILITY", "POLICY_QA", "DELIVERY_ISSUE",
              "LOGISTICS_QUERY", "SELF_ANALYTICS", "PRODUCT_QUERY",
              "ORDER_QUERY", "UNKNOWN"]
    extra = sorted({row.gold for row in readings} - set(labels))
    for gold in [*labels, *extra]:
        rows = [row for row in readings if row.gold == gold]
        support = len(rows)
        if not support:
            continue
        hit = sum(1 for row in rows if row.predicted == gold)
        unknown = sum(1 for row in rows if row.predicted == "UNKNOWN")
        if gold == "UNKNOWN":
            # Saying UNKNOWN is the correct answer here, so it is not a
            # declined case; a different known label is the misread.
            per_intent[gold] = {
                "support": support,
                "correct": hit,
                "unknown": hit,
                "wrong": support - hit,
                "coverage": None,
                "accuracy": _rate(hit, support),
                "predicted": dict(
                    sorted(Counter(row.predicted for row in rows).items())
                ),
            }
            continue
        per_intent[gold] = {
            "support": support,
            "correct": hit,
            "unknown": unknown,
            "wrong": support - hit - unknown,
            "coverage": _rate(support - unknown, support),
            "accuracy": _rate(hit, support),
            "predicted": dict(
                sorted(Counter(row.predicted for row in rows).items())
            ),
        }

    return {
        "cases": total,
        "correct": correct,
        "wrong": wrong,
        "false_unknown": false_unknown,
        "false_unknown_write": false_unknown_write,
        "gold_unknown": gold_unknown,
        "unknown_predictions": unknown_predictions,
        "unknown_matched": unknown_matched,
        "unknown_misread": unknown_misread,
        "answerable": answerable,
        "coverage": _rate(total - unknown_predictions, total),
        "accuracy": _rate(correct, total),
        "answerable_accuracy": _rate(correct_answerable, answerable),
        "rule_latency_ms": round(sum(row.rule_latency_ms for row in readings), 3),
        "per_intent": per_intent,
        "cost_matrix": _cost_matrix(readings, total),
    }


def _all_cues() -> set[str]:
    cues: set[str] = set()
    for spec in get_catalog().ordered_specs():
        cues.update(cue.lower() for cue in spec.cues)
    return cues


def _cue_overlap(readings: Sequence[CaseReading]) -> dict[str, Any]:
    """How much of the external layer already shares a gold-intent cue.

    This is a visibility number, not a score: a phrase can naturally contain
    ``退款`` without being copied from the cue table. The score above is
    always the classifier's own output.
    """
    catalog = get_catalog()
    cues = _all_cues()
    hits = 0
    exact = 0
    by_intent: Counter[str] = Counter()
    for row in readings:
        lowered = row.text.lower().strip()
        if lowered in cues:
            exact += 1
        spec = catalog.get(row.gold)
        if any(cue in lowered for cue in spec.cues):
            hits += 1
            by_intent[row.gold] += 1
    return {
        "cases": len(readings),
        "with_gold_cue": hits,
        "rate": _rate(hits, len(readings)),
        "exact_cue_texts": exact,
        "by_intent": dict(sorted(by_intent.items())),
        "note": (
            "Diagnostic only. The score in this report is produced by "
            "classify_support; this rate is never added to it."
        ),
    }


def _model_gap(
    readings: Sequence[CaseReading],
    runner: ModelRunner,
    model_name: str,
) -> tuple[list[CaseReading], dict[str, Any]]:
    """Ask the model only where the rules said ``UNKNOWN``.

    The six-label contract cannot express fine intents such as
    ``REFUND_STATUS``; those cases are skipped and counted, never scored as
    model errors. A failure is a failed call, not a wrong answer.
    """
    gaps = [row for row in readings if row.predicted == "UNKNOWN"]
    asked = [row for row in gaps if row.gold in MODEL_LABEL_SET]
    skipped = [row for row in gaps if row.gold not in MODEL_LABEL_SET]

    updated: dict[str, CaseReading] = {}
    per_intent: dict[str, Counter[str]] = {}
    answered = 0
    failures = 0
    correct = 0
    model_unknown = 0
    invalid_output = 0
    order_expected = order_filled = 0
    amount_expected = amount_filled = 0
    order_fabrications: list[str] = []
    amount_fabrications: list[str] = []
    order_missing: list[str] = []
    amount_missing: list[str] = []
    latency_total = 0.0
    input_tokens = output_tokens = 0

    for row in asked:
        context = _slot_context(row.text)
        expectation = per_intent.setdefault(
            row.gold,
            Counter({"asked": 0, "answered": 0, "correct": 0, "unknown": 0,
                     "failed": 0}),
        )
        expectation["asked"] += 1
        try:
            reading = runner(row.text, context or None)
        except Exception:
            reading = ModelReading(intent="ERROR", status="FAILED")
        trace = {
            "called": True,
            "intent": reading.intent,
            "status": reading.status,
        }
        if reading.status != "OK":
            failures += 1
            expectation["failed"] += 1
            trace["outcome"] = "failed"
            updated[row.case_id] = replace(row, model=trace)
            continue
        if reading.intent not in MODEL_LABEL_SET:
            invalid_output += 1
            expectation["failed"] += 1
            trace["outcome"] = "invalid_label"
            updated[row.case_id] = replace(row, model=trace)
            continue

        answered += 1
        latency_total += reading.latency_ms
        input_tokens += reading.input_tokens
        output_tokens += reading.output_tokens
        expectation["answered"] += 1

        expected_order = extract_order_id(row.text)
        expected_amount, _ = extract_amount(row.text)
        if reading.intent == row.gold:
            correct += 1
            expectation["correct"] += 1
        if reading.intent == "UNKNOWN":
            model_unknown += 1
            expectation["unknown"] += 1

        order_ok = reading.order_id is None
        if expected_order is not None:
            order_expected += 1
            if reading.order_id == expected_order:
                order_filled += 1
            else:
                order_missing.append(row.case_id)
            order_ok = reading.order_id == expected_order
        if reading.order_id is not None and not order_ok:
            order_fabrications.append(row.case_id)

        amount_ok = reading.amount_cents is None
        if expected_amount is not None:
            amount_expected += 1
            if reading.amount_cents == expected_amount:
                amount_filled += 1
            else:
                amount_missing.append(row.case_id)
            amount_ok = reading.amount_cents == expected_amount
        if reading.amount_cents is not None and not amount_ok:
            amount_fabrications.append(row.case_id)

        trace.update(
            {
                "outcome": "answered",
                "correct": reading.intent == row.gold,
                "slots": {
                    "order_id": reading.order_id,
                    "amount_cents": reading.amount_cents,
                },
                "input_tokens": reading.input_tokens,
                "output_tokens": reading.output_tokens,
                "latency_ms": round(reading.latency_ms, 2),
            }
        )
        updated[row.case_id] = replace(row, model=trace)

    for row in skipped:
        updated[row.case_id] = replace(
            row,
            model={
                "called": False,
                "outcome": "out_of_label_space",
                "why": f"gold {row.gold} is not one of {list(MODEL_LABELS)}",
            },
        )
    for row in readings:
        if row.predicted != "UNKNOWN" and row.case_id not in updated:
            updated[row.case_id] = row

    cases = [updated[row.case_id] for row in readings]
    gap_by_layer = Counter(row.layer for row in gaps)
    asked_by_layer = Counter(row.layer for row in asked)
    skipped_by_layer = Counter(row.layer for row in skipped)

    report = {
        "model_name": model_name,
        "label_space": list(MODEL_LABELS),
        "gap_cases": len(gaps),
        "gap_by_layer": dict(sorted(gap_by_layer.items())),
        "asked": len(asked),
        "asked_by_layer": dict(sorted(asked_by_layer.items())),
        "calls": len(asked),
        "out_of_label_space": len(skipped),
        "out_of_label_space_by_layer": dict(sorted(skipped_by_layer.items())),
        "answered": answered,
        "failures": failures,
        "invalid_output": invalid_output,
        "coarse_correct": correct,
        "coarse_intent_accuracy": _rate(correct, answered),
        "model_unknown": model_unknown,
        "slot_fill": {
            "order_id": {
                "expected": order_expected,
                "filled": order_filled,
                "rate": _rate(order_filled, order_expected),
                "missing": order_missing,
                "note": (
                    "No rule-UNKNOWN case can carry an order id: an ORD- id "
                    "makes the rules answer ORDER_QUERY, so the gap subset has "
                    "a structural zero denominator here."
                ),
            },
            "amount_cents": {
                "expected": amount_expected,
                "filled": amount_filled,
                "rate": _rate(amount_filled, amount_expected),
                "missing": amount_missing,
            },
        },
        "order_id_fabrications": len(order_fabrications),
        "order_id_fabrication_ids": order_fabrications,
        "amount_fabrications": len(amount_fabrications),
        "amount_fabrication_ids": amount_fabrications,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tokens": input_tokens + output_tokens,
        "latency_ms": round(latency_total, 2),
        "avg_latency_ms": (
            round(latency_total / answered, 2) if answered else None
        ),
        "per_intent": {
            gold: {
                "asked": counts["asked"],
                "answered": counts["answered"],
                "correct": counts["correct"],
                "unknown": counts["unknown"],
                "failed": counts["failed"],
            }
            for gold, counts in sorted(per_intent.items())
        },
        "note": (
            "Asked only on the rule-UNKNOWN subset. Fine intents outside the "
            "six-value model contract are skipped, not scored. Failures are "
            "counted separately from wrong answers."
        ),
    }
    return cases, report


def _check_invariants(report: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    cases = report["cases"]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        failures.append("duplicate case ids")

    for layer_name, layer in report["layers"].items():
        total = layer["cases"]
        bucket_sum = sum(
            layer["cost_matrix"][bucket]["cases"] for bucket in COST_BUCKETS
        )
        if bucket_sum != total:
            failures.append(
                f"{layer_name}: cost buckets sum to {bucket_sum}, expected {total}"
            )
        support_sum = sum(
            row["support"] for row in layer["per_intent"].values()
        )
        if support_sum != total:
            failures.append(
                f"{layer_name}: per-intent support sums to {support_sum}, "
                f"expected {total}"
            )
        if layer["correct"] + layer["false_unknown"] + layer["wrong"] != total:
            failures.append(f"{layer_name}: correct/wrong/unknown do not sum")
        split_sum = sum(
            layer["cost_matrix"]["write_intent_error"]["breakdown"].values()
        )
        if split_sum != layer["cost_matrix"]["write_intent_error"]["cases"]:
            failures.append(
                f"{layer_name}: write splits sum to {split_sum}, expected "
                f"{layer['cost_matrix']['write_intent_error']['cases']}"
            )
        if layer["false_unknown_write"] > layer["false_unknown"]:
            failures.append(
                f"{layer_name}: write false-unknown exceeds all false-unknown"
            )
        if (
            layer["unknown_predictions"]
            != layer["unknown_matched"] + layer["false_unknown"]
        ):
            failures.append(f"{layer_name}: unknown prediction split does not sum")
        if layer["gold_unknown"] != layer["unknown_matched"] + layer["unknown_misread"]:
            failures.append(f"{layer_name}: gold UNKNOWN split does not sum")

    overlap = report["cue_overlap"]
    if overlap["exact_cue_texts"]:
        failures.append(
            f"corpus copies {overlap['exact_cue_texts']} cue(s) verbatim"
        )
    if overlap["with_gold_cue"] > overlap["cases"]:
        failures.append("cue overlap exceeds the corpus size")

    for case in cases:
        if case["bucket"] not in COST_BUCKETS:
            failures.append(f"{case['id']}: unknown cost bucket {case['bucket']}")
        if case["predicted"] != "UNKNOWN" and case["model"] is not None:
            if case["model"].get("called"):
                failures.append(f"{case['id']}: model called outside the gap subset")

    model = report.get("model_gap")
    if model is not None:
        if model["calls"] != model["asked"]:
            failures.append("model calls do not equal the asked subset")
        if sum(model["asked_by_layer"].values()) != model["asked"]:
            failures.append("model asked-by-layer does not sum to asked")
        if model["answered"] + model["failures"] + model["invalid_output"] != model[
            "asked"
        ]:
            failures.append("model answered/failed/invalid do not sum to asked")

    return {"ok": not failures, "failures": failures}


def evaluate(
    dataset: dict[str, Any],
    *,
    runner: ModelRunner | None = None,
    model_name: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """Score the dataset. ``runner`` is the only way a model is reached."""
    catalog_service = CatalogService()
    # Warm the lazy catalog and product index once, so the per-layer latency
    # does not charge the first case for one-time initialisation.
    _read_rule("预热", catalog_service)
    readings = [_read_case(case, catalog_service) for case in dataset["cases"]]

    if runner is not None:
        readings, model_report = _model_gap(readings, runner, model_name)
    else:
        model_report = None

    layers: dict[str, Any] = {}
    for layer_name in sorted({row.layer for row in readings}):
        layers[layer_name] = _layer_report(
            layer_name, [row for row in readings if row.layer == layer_name]
        )

    external = [row for row in readings if row.layer == "external"]
    report = {
        "version": dataset.get("version"),
        "dataset": dataset.get("description"),
        "label_rule": dataset.get("label_rule"),
        "cases": [row.as_dict() for row in readings],
        "layers": layers,
        "cue_overlap": _cue_overlap(external),
        "model_gap": model_report,
        "cost_note": (
            "The rule path calls no model, so tokens are 0 in every cost bucket. "
            "A refund amount is amount_exposed_cents (the user typed it on a turn "
            "routed to REFUND_REQUEST), never an amount paid; approvals are the "
            "upper bound for a refund task that has all its slots."
        ),
    }
    report["invariants"] = _check_invariants(report)
    return report


def lmstudio_runner(model_name: str) -> ModelRunner:
    """The real model path: pydantic-ai against the configured endpoint."""
    from app.agent.pydantic_agent import IntentAgent, ModelCallError

    agent = IntentAgent(model_name=model_name)

    def run(text: str, context: dict[str, Any] | None) -> ModelReading:
        try:
            output, trace = agent.parse(text, context)
        except ModelCallError as exc:
            return ModelReading(
                intent="ERROR",
                status="FAILED",
                latency_ms=exc.trace.latency_ms,
            )
        except Exception:
            return ModelReading(intent="ERROR", status="FAILED")
        return ModelReading(
            intent=output.intent,
            order_id=output.order_id,
            amount_cents=output.amount_cents,
            status=trace.status,
            input_tokens=trace.input_tokens,
            output_tokens=trace.output_tokens,
            latency_ms=trace.latency_ms,
        )

    return run


# ------------------------------------------------------------------ rendering
def _fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _fmt_money(cents: int) -> str:
    return f"{cents / 100:.2f}"


def _format_layer(name: str, layer: dict[str, Any]) -> list[str]:
    lines = [
        f"Layer: {name} ({layer['cases']} cases)",
        (
            f"  coverage(non-UNKNOWN) {_fmt_rate(layer['coverage'])}"
            f" | accuracy {_fmt_rate(layer['accuracy'])}"
            f" | answerable accuracy {_fmt_rate(layer['answerable_accuracy'])}"
        ),
        (
            f"  correct {layer['correct']} | wrong {layer['wrong']}"
            f" | false_unknown {layer['false_unknown']}"
            f" (of which write requests: {layer['false_unknown_write']})"
            f" | gold UNKNOWN {layer['gold_unknown']}"
            f" | predicted UNKNOWN {layer['unknown_predictions']}"
        ),
        "  per intent (gold):",
    ]
    for gold, row in layer["per_intent"].items():
        lines.append(
            f"    {gold:<20} n={row['support']:<4} correct={row['correct']:<4}"
            f" unknown={row['unknown']:<4} wrong={row['wrong']:<4}"
            f" coverage={_fmt_rate(row['coverage'])}"
            f" accuracy={_fmt_rate(row['accuracy'])}"
        )
    lines.append("  cost matrix:")
    for bucket in COST_BUCKETS:
        cost = layer["cost_matrix"][bucket]
        lines.append(
            f"    {bucket:<20} n={cost['cases']:<4}"
            f" share={_fmt_rate(cost['share'])}"
            f" clarify={cost['clarifications']:<4}"
            f" tasks={cost['tasks']:<4}"
            f" approvals={cost['approvals']:<4}"
            f" handoffs={cost['handoffs']:<4}"
            f" amount_exposed={_fmt_money(cost['amount_exposed_cents'])}"
        )
        breakdown = cost.get("breakdown")
        if breakdown:
            lines.append(
                "      breakdown: "
                + " | ".join(
                    f"{name} {breakdown[name]}" for name in WRITE_SPLITS
                )
            )
    lines.append(
        f"  rule latency {layer['rule_latency_ms']:.3f} ms"
        " (rules call no model: tokens=0 in every bucket)"
    )
    return lines


def _format_model(model: dict[str, Any]) -> list[str]:
    lines = [
        "Model gap (rule-UNKNOWN subset only; six-value label space)",
        (
            f"  gap cases {model['gap_cases']}"
            f" | asked {model['asked']} | calls {model['calls']}"
            f" | answered {model['answered']}"
            f" | failures {model['failures']}"
            f" | invalid labels {model['invalid_output']}"
        ),
        (
            f"  out_of_label_space {model['out_of_label_space']}"
            " (skipped, not scored)"
        ),
        (
            f"  coarse intent accuracy {model['coarse_correct']}"
            f"/{model['answered']} = {_fmt_rate(model['coarse_intent_accuracy'])}"
            f" | model said UNKNOWN {model['model_unknown']}"
        ),
        (
            "  slot fill: order_id "
            f"{model['slot_fill']['order_id']['filled']}"
            f"/{model['slot_fill']['order_id']['expected']}"
            f" ({_fmt_rate(model['slot_fill']['order_id']['rate'])})"
            " | amount "
            f"{model['slot_fill']['amount_cents']['filled']}"
            f"/{model['slot_fill']['amount_cents']['expected']}"
            f" ({_fmt_rate(model['slot_fill']['amount_cents']['rate'])})"
        ),
        (
            f"  fabrications: order_id {model['order_id_fabrications']}"
            f" | amount {model['amount_fabrications']}"
        ),
        (
            f"  tokens {model['tokens']}"
            f" | latency {model['latency_ms'] / 1000:.1f} s"
            f" (avg {model['avg_latency_ms']} ms/call)"
        ),
    ]
    if model["slot_fill"]["order_id"]["expected"] == 0:
        lines.append(
            "  note: no rule-UNKNOWN case can carry an order id; the rules would "
            "have answered ORDER_QUERY."
        )
    return lines


def format_report(report: dict[str, Any]) -> str:
    lines = [
        (
            f"Intent corpus: {DATASET_PATH} "
            f"({len(report['cases'])} cases; layers are reported separately, "
            "never merged into one accuracy)"
        ),
        "",
    ]
    for name, layer in report["layers"].items():
        lines.extend(_format_layer(name, layer))
        lines.append("")
    overlap = report["cue_overlap"]
    lines.append(
        f"Cue overlap (external): {overlap['with_gold_cue']}/{overlap['cases']}"
        f" = {_fmt_rate(overlap['rate'])} contain a gold-intent cue"
        f" (reported, not scored; exact cue copies: {overlap['exact_cue_texts']})"
    )
    lines.append("")
    model = report["model_gap"]
    if model is None:
        lines.append("Model gap: not measured (pass --model to measure it).")
    else:
        lines.extend(_format_model(model))
    lines.append("")
    lines.append(report["cost_note"])
    invariants = report["invariants"]
    lines.append("")
    if invariants["ok"]:
        lines.append("INVARIANTS OK")
    else:
        lines.append("INVARIANTS FAILED")
        for failure in invariants["failures"]:
            lines.append(f"  - {failure}")
    return "\n".join(lines)


def gate_failures(
    report: dict[str, Any], *, model_enabled: bool = False
) -> list[str]:
    """Everything that should stop ``--enforce`` with a non-zero exit."""
    failures = list(report["invariants"]["failures"])
    model = report["model_gap"]
    if model_enabled and model is not None:
        if model["invalid_output"]:
            failures.append(
                f"model emitted {model['invalid_output']} label(s) outside its "
                f"schema: {list(MODEL_LABELS)}"
            )
        if model["order_id_fabrications"]:
            failures.append(
                f"model fabricated {model['order_id_fabrications']} order id(s)"
            )
        if model["amount_fabrications"]:
            failures.append(
                f"model fabricated {model['amount_fabrications']} amount(s)"
            )
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline TicketPilot intent evaluation."
    )
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument(
        "--json",
        nargs="?",
        const="-",
        metavar="PATH",
        help="Write JSON to PATH; with no PATH, print it to stdout.",
    )
    parser.add_argument(
        "--enforce",
        action="store_true",
        help=(
            "Exit 1 when a structural invariant fails, or when the model emits "
            "a label outside its schema or fabricates a slot."
        ),
    )
    parser.add_argument(
        "--model",
        action="store_true",
        help="Ask the model on the rule-UNKNOWN subset (needs an endpoint).",
    )
    parser.add_argument("--model-name", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    runner = lmstudio_runner(args.model_name) if args.model else None
    report = evaluate(dataset, runner=runner, model_name=args.model_name)

    if args.json:
        payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.json == "-":
            print(payload, end="")
        else:
            path = Path(args.json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8", newline="\n")
            print(f"wrote {path}")
    else:
        print(format_report(report))

    failures = gate_failures(report, model_enabled=args.model)
    if args.enforce and failures:
        print("GATE FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
