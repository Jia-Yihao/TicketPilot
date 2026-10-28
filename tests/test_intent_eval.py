"""Pins the intent corpus, its labels and the metric definitions.

The tests do not assert that the classifier is good. They assert that the
report is honest: the corpus is traceable and human-labelled, no case copies a
routing cue, the layers are never merged, ``UNKNOWN`` is not counted as a
misread, every case lands in one cost bucket, and the model is asked only on
the rule-``UNKNOWN`` gap.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

import evals.intent_metrics as metrics
from app.agent.intent_catalog import get_catalog
from app.agent.routing import classify, extract_amount, extract_order_id
from app.agent.support_agent import classify_support
from app.services.catalog import CatalogService
from evals.intent_metrics import (
    COST_BUCKETS,
    DATASET_PATH,
    MODEL_LABEL_SET,
    WRITE_INTENTS,
    WRITE_SPLITS,
    ModelReading,
    ModelRunner,
    _bucket,
    _write_split,
    evaluate,
    format_report,
    gate_failures,
)
from scripts.build_intent_set import LABELS, MIN_PER_INTENT, build, render

ROOT = Path(__file__).resolve().parent.parent
INTENT_DIR = ROOT / "evals" / "intent"
EXTERNAL_PATH = INTENT_DIR / "external_phrasings.jsonl"
MANIFEST_PATH = INTENT_DIR / "external_manifest.json"


def _read_dataset() -> dict[str, Any]:
    dataset: dict[str, Any] = json.loads(
        DATASET_PATH.read_text(encoding="utf-8")
    )
    return dataset


@pytest.fixture(scope="module")
def dataset() -> dict[str, Any]:
    return _read_dataset()


@pytest.fixture(scope="module")
def report(dataset: dict[str, Any]) -> dict[str, Any]:
    return evaluate(dataset)


def _layer_cases(report: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [case for case in report["cases"] if case["layer"] == name]


# --------------------------------------------------------------------- corpus
def test_committed_dataset_matches_its_sources() -> None:
    """The checked-in file is exactly what the builder produces."""
    assert DATASET_PATH.read_text(encoding="utf-8") == render(build())


def test_builder_check_mode_returns_zero() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/build_intent_set.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_dataset_declares_two_separate_layers(dataset: dict[str, Any]) -> None:
    assert set(dataset["layers"]) == {"external", "authored_gap"}
    assert {layer["label_source"] for layer in dataset["layers"].values()} == {
        "human"
    }
    assert dataset["labels"] == list(LABELS)
    assert dataset["min_per_intent"] == MIN_PER_INTENT
    assert "intents.yaml" in dataset["label_rule"]
    assert "do not add them into one accuracy" in dataset["description"]
    assert dict(Counter(case["layer"] for case in dataset["cases"])) == {
        name: layer["cases"] for name, layer in dataset["layers"].items()
    }


def test_every_intent_has_a_real_denominator(dataset: dict[str, Any]) -> None:
    counts = Counter(case["gold"] for case in dataset["cases"])

    assert set(counts) == set(LABELS)
    assert all(counts[label] >= MIN_PER_INTENT for label in LABELS), counts
    assert dataset["per_intent"] == dict(sorted(counts.items()))


def test_case_ids_and_texts_are_unique_across_layers(
    dataset: dict[str, Any],
) -> None:
    cases = dataset["cases"]

    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["text"] for case in cases}) == len(cases)


def test_every_case_is_human_labelled_with_provenance(
    dataset: dict[str, Any],
) -> None:
    for case in dataset["cases"]:
        assert case["label_source"] == "human", case["id"]
        assert case["note"].strip(), case["id"]
        if case["layer"] == "external":
            assert {"dataset", "file", "line"} <= set(case["provenance"]), case["id"]
            assert case["provenance"]["line"] > 0, case["id"]
        elif "authored" in case["provenance"]:
            assert case["provenance"] == {
                "authored": "intent-corpus-eval-2026-10"
            }, case["id"]
        else:
            # A closed intent gap enters this same layer; its provenance
            # names the queue row instead of the authored batch.
            assert case["provenance"]["source"] == "intent-gap-loop", case["id"]
            assert case["provenance"]["gap_id"] == case["id"], case["id"]
            assert case["provenance"]["cycle"], case["id"]


def test_external_phrasings_are_de_identified(dataset: dict[str, Any]) -> None:
    external = [case for case in dataset["cases"] if case["layer"] == "external"]

    for case in external:
        text = case["text"]
        assert not re.search(r"\d{6,}", text), case["id"]
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), case["id"]
        assert not re.search(r"https?://|www\.", text), case["id"]


def test_external_sample_hash_and_licence_are_declared() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    sample = EXTERNAL_PATH.read_bytes()

    assert manifest["license"] == "Apache-2.0"
    assert manifest["dataset_url"].startswith("https://www.modelscope.cn/datasets/")
    assert hashlib.sha256(sample).hexdigest() == manifest["sample_sha256"]
    assert manifest["sample_rows"] == len(
        [line for line in sample.decode("utf-8").splitlines() if line.strip()]
    )
    assert "human" in manifest["labels"]


def test_no_case_copies_a_routing_cue_verbatim(dataset: dict[str, Any]) -> None:
    """The new corpus must not be the cue table written in a different order."""
    cues = {
        cue.strip().lower()
        for spec in get_catalog().ordered_specs()
        for cue in spec.cues
    }

    for case in dataset["cases"]:
        assert case["text"].strip().lower() not in cues, case["id"]


# -------------------------------------------------------------------- metrics
def test_metrics_import_only_the_public_classifier() -> None:
    tree = ast.parse(inspect.getsource(metrics))
    imports: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.setdefault(node.module, set()).update(
                alias.name for alias in node.names
            )

    assert imports["app.agent.support_agent"] == {"classify_support"}
    assert imports["app.agent.intent_catalog"] == {"get_catalog"}
    assert imports["app.agent.routing"] <= {
        "RoutePlan",
        "classify",
        "extract_amount",
        "extract_order_id",
    }


def test_readings_match_the_public_entry_point(
    dataset: dict[str, Any], report: dict[str, Any]
) -> None:
    """The evaluator reads every message through the same two calls."""
    catalog = CatalogService()
    by_id = {case["id"]: case for case in dataset["cases"]}

    for reading in report["cases"]:
        text = by_id[reading["id"]]["text"]
        context: dict[str, Any] = {}
        order_id = extract_order_id(text)
        amount_cents, _ = extract_amount(text)
        if order_id:
            context["order_id"] = order_id
        if amount_cents is not None:
            context["amount_cents"] = amount_cents
        route = classify(text, context)
        plan = classify_support(
            text,
            route,
            has_catalog_match=catalog.resolve(text) is not None,
            message_order_id=extract_order_id(text),
            order_id=route.order_id,
        )
        assert reading["predicted"] == plan.intent.value, reading["id"]


def test_unknown_is_not_counted_as_wrong(report: dict[str, Any]) -> None:
    assert _bucket("ORDER_QUERY", "ORDER_QUERY") == "correct"
    assert _bucket("ORDER_QUERY", "UNKNOWN") == "false_unknown"
    assert _bucket("REFUND_REQUEST", "ORDER_QUERY") == "write_intent_error"
    assert _bucket("ORDER_QUERY", "LOGISTICS_QUERY") == "read_intent_error"
    assert _bucket("UNKNOWN", "ORDER_QUERY") == "read_intent_error"

    for name, layer in report["layers"].items():
        assert (
            layer["correct"] + layer["false_unknown"] + layer["wrong"]
            == layer["cases"]
        ), name
        assert (
            layer["unknown_predictions"]
            == layer["unknown_matched"] + layer["false_unknown"]
        ), name
        assert (
            layer["gold_unknown"]
            == layer["unknown_matched"] + layer["unknown_misread"]
        ), name
        unknown_row = layer["per_intent"]["UNKNOWN"]
        assert unknown_row["unknown"] == unknown_row["correct"]
        assert unknown_row["wrong"] == unknown_row["support"] - unknown_row["correct"]
        assert unknown_row["coverage"] is None


def test_layers_are_reported_side_by_side(report: dict[str, Any]) -> None:
    assert set(report["layers"]) == {"external", "authored_gap"}
    assert "accuracy" not in report
    for name, layer in report["layers"].items():
        assert layer["cases"] == len(_layer_cases(report, name))
        assert layer["cases"] > 0


def test_cost_matrix_is_exhaustive(report: dict[str, Any]) -> None:
    for name, layer in report["layers"].items():
        matrix = layer["cost_matrix"]
        assert set(matrix) == set(COST_BUCKETS), name
        assert sum(matrix[bucket]["cases"] for bucket in COST_BUCKETS) == layer[
            "cases"
        ], name
        assert sum(matrix[bucket]["share"] for bucket in COST_BUCKETS) == pytest.approx(
            1.0, abs=1e-3
        ), name
        assert matrix["false_unknown"]["tasks"] == 0, name
        assert matrix["false_unknown"]["approvals"] == 0, name


def test_write_errors_are_split_by_direction(report: dict[str, Any]) -> None:
    assert _write_split("ORDER_QUERY", "REFUND_REQUEST") == "extra_write"
    assert _write_split("REFUND_REQUEST", "ORDER_QUERY") == "missed_write"
    assert _write_split("REFUND_REQUEST", "TICKET_CREATE") == "write_to_write"
    assert _write_split("ORDER_QUERY", "LOGISTICS_QUERY") is None
    assert _write_split("REFUND_REQUEST", "REFUND_REQUEST") is None

    for name, layer in report["layers"].items():
        write = layer["cost_matrix"]["write_intent_error"]
        assert set(write["breakdown"]) == set(WRITE_SPLITS), name
        assert sum(write["breakdown"].values()) == write["cases"], name


def test_write_requests_declined_are_reported_separately(
    report: dict[str, Any],
) -> None:
    for name, layer in report["layers"].items():
        expected = sum(
            1
            for case in _layer_cases(report, name)
            if case["predicted"] == "UNKNOWN" and case["gold"] in WRITE_INTENTS
        )
        assert layer["false_unknown_write"] == expected, name
        assert layer["false_unknown_write"] <= layer["false_unknown"], name


def test_cue_overlap_is_reported_but_never_scored(report: dict[str, Any]) -> None:
    overlap = report["cue_overlap"]

    assert overlap["cases"] == report["layers"]["external"]["cases"]
    assert overlap["exact_cue_texts"] == 0
    assert 0 <= overlap["with_gold_cue"] <= overlap["cases"]
    assert "never added to it" in overlap["note"]


def test_format_report_states_the_separation(report: dict[str, Any]) -> None:
    text = format_report(report)

    assert "layers are reported separately" in text
    assert "never merged into one accuracy" in text
    assert "breakdown: extra_write" in text
    assert "INVARIANTS OK" in text
    assert "Model gap: not measured" in text


# ---------------------------------------------------------------- model gap
def _honest_runner(
    dataset: dict[str, Any], calls: list[str]
) -> ModelRunner:
    gold_by_text = {case["text"]: case["gold"] for case in dataset["cases"]}

    def run(text: str, context: dict[str, Any] | None) -> ModelReading:
        calls.append(text)
        amount_cents, _ = extract_amount(text)
        return ModelReading(
            intent=gold_by_text[text],
            amount_cents=amount_cents,
            status="OK",
            input_tokens=11,
            output_tokens=4,
            latency_ms=2.0,
        )

    return run


def test_no_runner_means_no_model_reading(report: dict[str, Any]) -> None:
    assert report["model_gap"] is None
    assert all(case["model"] is None for case in report["cases"])


def test_model_is_asked_only_on_the_label_space_gap(
    dataset: dict[str, Any], report: dict[str, Any]
) -> None:
    calls: list[str] = []
    modeled = evaluate(dataset, runner=_honest_runner(dataset, calls))
    gap = {
        case["text"]
        for case in report["cases"]
        if case["predicted"] == "UNKNOWN" and case["gold"] in MODEL_LABEL_SET
    }
    skipped = [
        case
        for case in report["cases"]
        if case["predicted"] == "UNKNOWN" and case["gold"] not in MODEL_LABEL_SET
    ]

    assert set(calls) == gap
    assert len(calls) == modeled["model_gap"]["asked"]
    assert modeled["model_gap"]["calls"] == modeled["model_gap"]["asked"]
    assert modeled["model_gap"]["out_of_label_space"] == len(skipped)
    assert set(modeled["model_gap"]["asked_by_layer"]) <= set(
        modeled["layers"]
    )
    for case in modeled["cases"]:
        if case["predicted"] != "UNKNOWN":
            assert case["model"] is None, case["id"]


def test_model_slot_fill_and_fabrication_are_separate(
    dataset: dict[str, Any]
) -> None:
    calls: list[str] = []
    modeled = evaluate(dataset, runner=_honest_runner(dataset, calls))
    gap = modeled["model_gap"]

    assert gap["order_id_fabrications"] == 0
    assert gap["amount_fabrications"] == 0
    assert gap["slot_fill"]["order_id"]["expected"] == 0
    assert gap["slot_fill"]["amount_cents"]["expected"] > 0
    assert gap["slot_fill"]["amount_cents"]["rate"] == 1.0
    assert gap["coarse_intent_accuracy"] == 1.0


def test_model_failure_is_not_a_wrong_answer(dataset: dict[str, Any]) -> None:
    gold_by_text = {case["text"]: case["gold"] for case in dataset["cases"]}
    failed: list[str] = []

    def run(text: str, context: dict[str, Any] | None) -> ModelReading:
        if not failed:
            failed.append(text)
            raise RuntimeError("endpoint down")
        amount_cents, _ = extract_amount(text)
        return ModelReading(
            intent=gold_by_text[text], amount_cents=amount_cents, status="OK"
        )

    modeled = evaluate(dataset, runner=run)
    gap = modeled["model_gap"]

    assert gap["failures"] == 1
    assert gap["asked"] > gap["answered"] == gap["asked"] - 1
    assert sum(row["failed"] for row in gap["per_intent"].values()) == 1
    assert gap["coarse_correct"] <= gap["answered"]
    assert not gate_failures(modeled, model_enabled=True)


def test_invalid_model_label_is_counted_outside_the_score(
    dataset: dict[str, Any]
) -> None:
    def run(text: str, context: dict[str, Any] | None) -> ModelReading:
        return ModelReading(intent="SELF_ANALYTICS", status="OK")

    modeled = evaluate(dataset, runner=run)
    gap = modeled["model_gap"]

    assert gap["invalid_output"] == gap["asked"]
    assert gap["answered"] == 0
    assert gap["coarse_intent_accuracy"] is None
    assert sum(row["failed"] for row in gap["per_intent"].values()) == gap["asked"]
    assert gate_failures(modeled, model_enabled=True)


def test_model_slot_fabrication_fails_the_gate(dataset: dict[str, Any]) -> None:
    gold_by_text = {case["text"]: case["gold"] for case in dataset["cases"]}

    def run(text: str, context: dict[str, Any] | None) -> ModelReading:
        return ModelReading(intent=gold_by_text[text], order_id="ORD-FAKE-0001")

    modeled = evaluate(dataset, runner=run)
    gap = modeled["model_gap"]

    assert gap["order_id_fabrications"] == gap["asked"]
    assert any("fabricated" in item for item in gate_failures(
        modeled, model_enabled=True
    ))


def test_enforce_passes_on_the_committed_corpus() -> None:
    assert metrics.main(["--enforce", "--dataset", str(DATASET_PATH)]) == 0
