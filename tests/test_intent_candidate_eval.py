"""The P8 candidate evidence harness is measured, not narrated.

`evals/intent_candidates.py` replays the shipped intent corpus through the
deterministic candidate kernel and reports what the server *would have
offered* the model. These tests pin the four things that make that report
worth reading: the two layers are never merged into one number, the
narrowed sets and the no-signal sets get their own denominators (a
whole-catalog set is not a successful shortlist), the bucket distribution
carries no verdict, and an empty corpus refuses to produce a report.

Nothing here asserts that the model picks well: that number needs a real
endpoint and is reported as untested by the harness itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import evals.intent_candidates as harness
from evals.intent_candidates import (
    DATASET_PATH,
    LAYER_KEYS,
    TOP_KEYS,
    GapAnswer,
    GapRequest,
    evaluate,
    format_report,
    load_dataset,
    main,
)

CORPUS_LAYERS = {"external", "authored_gap"}


def _case(case_id: str, text: str, gold: str, layer: str = "external") -> dict[str, Any]:
    return {
        "id": case_id,
        "text": text,
        "gold": gold,
        "layer": layer,
        "label_source": "human",
        "note": "",
        "provenance": None,
    }


def _dataset(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": "test", "description": "tiny", "cases": cases}


@pytest.fixture(scope="module")
def report() -> dict[str, Any]:
    return evaluate(load_dataset(DATASET_PATH))


def test_the_report_shape_is_fixed(report: dict[str, Any]) -> None:
    assert set(report) == {
        "version",
        "corpus",
        "config",
        "layers",
        "model",
        "notes",
    }
    assert set(report["layers"]) == CORPUS_LAYERS
    for layer in report["layers"].values():
        assert set(layer) == LAYER_KEYS
        assert set(layer["narrowed"]) == {
            "sets",
            "share",
            "coverage",
            "avg_candidates",
            "cap_hits",
        }
        assert set(layer["no_signal"]) == {"sets", "share", "avg_candidates"}
        assert set(layer["buckets"]) == {"high", "medium", "low"}
        assert set(layer["unconditional"]) == {"human_handoff", "unknown"}


def test_the_corpus_is_accounted_for_per_layer(report: dict[str, Any]) -> None:
    corpus = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    assert report["corpus"]["cases"] == len(corpus["cases"])
    assert report["corpus"]["catalog_rows"] == 12
    # Every case lands in exactly one layer and no layer-level number is
    # duplicated at the top: there is no merged coverage to read.
    assert sum(layer["cases"] for layer in report["layers"].values()) == len(corpus["cases"])
    assert "coverage" not in report
    assert "avg_candidates" not in report


def test_the_two_set_kinds_split_the_same_cases(report: dict[str, Any]) -> None:
    for layer in report["layers"].values():
        assert layer["narrowed"]["sets"] + layer["no_signal"]["sets"] == layer["cases"]
        assert layer["narrowed"]["share"] + layer["no_signal"]["share"] == pytest.approx(1.0)


def test_a_no_signal_set_is_not_counted_as_a_successful_shortlist() -> None:
    """One missed shortlist plus one blind sentence: coverage is 0, not 1/2."""
    report = evaluate(
        _dataset(
            [
                _case("miss", "物流到哪了", "PRODUCT_QUERY"),
                _case("blind", "在吗在吗", "REFUND_REQUEST"),
            ]
        )
    )
    layer = report["layers"]["external"]

    assert layer["narrowed"]["sets"] == 1
    assert layer["narrowed"]["coverage"] == 0.0
    assert layer["no_signal"]["sets"] == 1
    assert layer["no_signal"]["avg_candidates"] == 12.0
    # The blind sentence still shows the gold, but that is the whole catalog
    # talking, not the shortlist: it is excluded from the coverage numerator
    # and denominator, and reported as its own share instead.
    assert layer["gold_bucket"]["absent"] == 1
    assert layer["gold_bucket"]["high"] + layer["gold_bucket"]["medium"] == 0


def test_coverage_uses_the_narrowed_sets_as_its_denominator() -> None:
    report = evaluate(
        _dataset(
            [
                _case("hit", "物流到哪了", "LOGISTICS_QUERY"),
                _case("miss", "物流到哪了", "PRODUCT_QUERY"),
                _case("blind", "在吗在吗", "REFUND_REQUEST"),
            ]
        )
    )
    layer = report["layers"]["external"]

    assert layer["narrowed"]["sets"] == 2
    assert layer["narrowed"]["coverage"] == pytest.approx(0.5)
    # Rates are rounded to four decimals in the report, so compare at that precision.
    assert layer["no_signal"]["share"] == pytest.approx(1 / 3, abs=1e-4)


def test_an_empty_corpus_refuses_to_produce_a_report() -> None:
    with pytest.raises(ValueError):
        evaluate(_dataset([]))


def test_the_bucket_distribution_carries_no_verdict(report: dict[str, Any]) -> None:
    """Counts and shares only: no threshold, no pass/fail on how the buckets land."""
    assert not hasattr(harness, "gate_failures")
    assert not any(key in report for key in ("gate", "gates", "passed", "failures"))
    for layer in report["layers"].values():
        assert all(isinstance(value, int) for value in layer["buckets"].values())
        assert all(isinstance(value, int) for value in layer["gold_bucket"].values())
        assert "threshold" not in str(layer)


def test_the_fallback_rows_are_always_in_a_narrowed_set(report: dict[str, Any]) -> None:
    for layer in report["layers"].values():
        assert layer["narrowed"]["sets"] > 0
        assert layer["unconditional"]["human_handoff"] == 1.0
        assert layer["unconditional"]["unknown"] == 1.0


def test_the_cap_is_wired_into_the_report() -> None:
    report = evaluate(load_dataset(DATASET_PATH), max_candidates=1)

    assert report["config"]["max_candidates"] == 1
    for layer in report["layers"].values():
        assert layer["narrowed"]["avg_candidates"] == pytest.approx(3.0)
        assert layer["no_signal"]["avg_candidates"] == 12.0


def test_running_it_twice_gives_the_same_bytes(report: dict[str, Any]) -> None:
    again = evaluate(load_dataset(DATASET_PATH))

    assert json.dumps(report, sort_keys=True) == json.dumps(again, sort_keys=True)
    assert "generated_at" not in report


def test_the_human_report_keeps_the_layers_apart_and_flags_what_is_untested(
    report: dict[str, Any],
) -> None:
    text = format_report(report)

    assert "external" in text and "authored_gap" in text
    assert "coverage" in text
    assert any("未测" in note for note in report["notes"])
    assert "未测" in text


def test_the_cli_writes_machine_readable_output(tmp_path: Path) -> None:
    payload = tmp_path / "candidates.json"

    assert main(["--json", str(payload)]) == 0

    written = json.loads(payload.read_text(encoding="utf-8"))
    assert set(written) == TOP_KEYS | {"model"}
    assert written["corpus"] == evaluate(load_dataset(DATASET_PATH))["corpus"]


def _answer(
    case_id: str,
    *,
    narrowed: bool,
    on_route: str,
    on_reason: str | None,
    off_route: str,
) -> GapAnswer:
    return GapAnswer(
        case_id=case_id,
        narrowed=narrowed,
        on_route=on_route,
        on_reason=on_reason,
        on_source="pydantic_ai",
        on_requests=1,
        off_route=off_route,
        off_requests=1,
    )


def test_the_model_block_is_marked_untested_without_an_endpoint(report: dict[str, Any]) -> None:
    model = report["model"]

    assert model["status"] == "untested"
    assert model["sampled"] == 0
    assert model["gap_cases"] > 0
    assert model["cases"] == []


def test_only_the_sentences_the_rules_cannot_read_reach_the_model() -> None:
    asked: list[str] = []

    def runner(request: GapRequest) -> GapAnswer:
        asked.append(request.case_id)
        return _answer(
            request.case_id,
            narrowed=request.narrowed,
            on_route="REFUND_REQUEST",
            on_reason=None,
            off_route="REFUND_REQUEST",
        )

    report = evaluate(
        _dataset(
            [
                _case("blind", "在吗在吗", "REFUND_REQUEST"),
                _case("read-logic", "物流到哪了", "LOGISTICS_QUERY"),
                _case("read-refund", "我要退钱", "REFUND_REQUEST"),
            ]
        ),
        runner=runner,
        model_name="stub",
    )

    assert asked == ["blind"]
    assert report["model"]["status"] == "measured"
    assert report["model"]["gap_cases"] == 1
    assert report["model"]["sampled"] == 1


def test_the_model_a_b_separates_out_of_candidates_from_none_from_a_route_change() -> None:
    answers = {
        # Inside the shortlist: the reading is used exactly as before.
        "g1": _answer(
            "g1",
            narrowed=False,
            on_route="REFUND_REQUEST",
            on_reason=None,
            off_route="REFUND_REQUEST",
        ),
        # A row the server never offered: dropped, and the old path would have kept it.
        "g2": _answer(
            "g2",
            narrowed=False,
            on_route="UNKNOWN",
            on_reason="model_out_of_candidates",
            off_route="REFUND_REQUEST",
        ),
        # An honest NONE: no adoption, no task, no second request.
        "g3": _answer(
            "g3",
            narrowed=False,
            on_route="UNKNOWN",
            on_reason="model_none",
            off_route="UNKNOWN",
        ),
    }

    def runner(request: GapRequest) -> GapAnswer:
        return answers[request.case_id]

    report = evaluate(
        _dataset(
            [
                _case("g1", "在吗在吗", "REFUND_REQUEST"),
                _case("g2", "我的钱呢", "REFUND_REQUEST"),
                _case("g3", "这个单子怎么搞", "REFUND_REQUEST"),
            ]
        ),
        runner=runner,
        model_name="stub",
    )
    model = report["model"]

    assert model["out_of_candidates"] == {"cases": 1, "share": pytest.approx(1 / 3, abs=1e-4)}
    assert model["none"] == {"cases": 1, "share": pytest.approx(1 / 3, abs=1e-4)}
    assert model["route_changed"] == {"cases": 1, "share": pytest.approx(1 / 3, abs=1e-4)}
    assert model["gold_agreement_on"] == pytest.approx(1 / 3, abs=1e-4)
    assert model["avg_requests_on"] == pytest.approx(1.0)
    assert [row["id"] for row in model["cases"]] == ["g1", "g2", "g3"]
    assert set(model["cases"][0]) == {
        "id",
        "layer",
        "narrowed",
        "gold",
        "candidates",
        "on_route",
        "on_reason",
        "off_route",
        "changed",
    }


def test_the_sample_is_bounded_and_names_its_denominator() -> None:
    asked: list[str] = []

    def runner(request: GapRequest) -> GapAnswer:
        asked.append(request.case_id)
        return _answer(
            request.case_id,
            narrowed=request.narrowed,
            on_route="UNKNOWN",
            on_reason="model_none",
            off_route="UNKNOWN",
        )

    report = evaluate(
        _dataset([_case(f"g{i}", "在吗在吗", "REFUND_REQUEST") for i in range(5)]),
        runner=runner,
        model_name="stub",
        model_limit=2,
    )

    assert asked == ["g0", "g1"]
    assert report["model"]["gap_cases"] == 5
    assert report["model"]["sampled"] == 2
    assert report["model"]["none"]["share"] == pytest.approx(1.0)


def test_a_bounded_sample_still_covers_every_layer() -> None:
    """The corpus is grouped by layer, so "the first N" would sample one layer only."""
    asked: list[tuple[str, str]] = []

    def runner(request: GapRequest) -> GapAnswer:
        asked.append((request.case_id, request.layer))
        return _answer(
            request.case_id,
            narrowed=request.narrowed,
            on_route="UNKNOWN",
            on_reason="model_none",
            off_route="UNKNOWN",
        )

    report = evaluate(
        _dataset(
            [
                _case("e1", "在吗在吗", "REFUND_REQUEST", "external"),
                _case("e2", "我的钱呢", "REFUND_REQUEST", "external"),
                _case("e3", "这个单子怎么搞", "REFUND_REQUEST", "external"),
                _case("a1", "东西还没到", "REFUND_REQUEST", "authored_gap"),
                _case("a2", "帮我查一下 ORD-DEMO-1001", "REFUND_REQUEST", "authored_gap"),
                _case("a3", "什么时候到货啊", "REFUND_REQUEST", "authored_gap"),
            ]
        ),
        runner=runner,
        model_name="stub",
        model_limit=2,
    )

    assert [case_id for case_id, _layer in asked] == ["e1", "a1"]
    assert {layer for _case_id, layer in asked} == {"external", "authored_gap"}
    assert report["model"]["gap_cases"] == 6
    assert report["model"]["sampled"] == 2


def test_the_sample_asks_the_binds_first() -> None:
    """A shortlist of one row cannot be violated by a whole-catalog set.

    Out-of-candidates can only happen inside a narrowed set, so the bounded
    sample spends its calls there first; the no-signal sentences follow.
    """
    asked: list[str] = []

    def runner(request: GapRequest) -> GapAnswer:
        asked.append(request.case_id)
        return _answer(
            request.case_id,
            narrowed=request.narrowed,
            on_route="UNKNOWN",
            on_reason="model_none",
            off_route="UNKNOWN",
        )

    report = evaluate(
        _dataset(
            [
                _case("blind-1", "在吗在吗", "REFUND_REQUEST"),
                _case("bind-1", "多大尺寸", "PRODUCT_QUERY"),
                _case("blind-2", "我的钱呢", "REFUND_REQUEST"),
            ]
        ),
        runner=runner,
        model_name="stub",
        model_limit=1,
    )

    assert asked == ["bind-1"]
    assert report["model"]["sampled"] == 1
    assert report["model"]["cases"][0]["narrowed"] is True


def test_the_sample_covers_both_kinds_of_set_when_there_are_two() -> None:
    """A blind set cannot violate the shortlist, but it still shows what the model names.

    So the bounded budget is split: half of it goes to the narrowed sets (where
    an out-of-candidates answer is even possible), the rest to the whole-catalog
    sets (where only the NONE answer and the model's own jitter are visible).
    """
    asked: list[tuple[str, bool]] = []

    def runner(request: GapRequest) -> GapAnswer:
        asked.append((request.case_id, request.narrowed))
        return _answer(
            request.case_id,
            narrowed=request.narrowed,
            on_route="UNKNOWN",
            on_reason="model_none",
            off_route="UNKNOWN",
        )

    report = evaluate(
        _dataset(
            [
                _case("blind-1", "在吗在吗", "REFUND_REQUEST"),
                _case("bind-1", "多大尺寸", "PRODUCT_QUERY"),
                _case("blind-2", "我的钱呢", "REFUND_REQUEST"),
                _case("bind-2", "材质不一样吗", "PRODUCT_QUERY"),
                _case("blind-3", "这个单子怎么搞", "REFUND_REQUEST"),
            ]
        ),
        runner=runner,
        model_name="stub",
        model_limit=4,
    )

    assert asked == [
        ("bind-1", True),
        ("bind-2", True),
        ("blind-1", False),
        ("blind-2", False),
    ]
    assert report["model"]["gap_cases"] == 5


def test_a_measured_run_stops_claiming_the_number_is_untested() -> None:
    def runner(request: GapRequest) -> GapAnswer:
        return _answer(
            request.case_id,
            narrowed=False,
            on_route="REFUND_REQUEST",
            on_reason=None,
            off_route="REFUND_REQUEST",
        )

    offline = evaluate(_dataset([_case("g1", "在吗在吗", "REFUND_REQUEST")]))
    measured = evaluate(
        _dataset([_case("g1", "在吗在吗", "REFUND_REQUEST")]), runner=runner, model_name="stub"
    )

    assert any("未测" in note for note in offline["notes"])
    assert not any("未测" in note for note in measured["notes"])
    assert any("sample" in note for note in measured["notes"])
    assert "未测" in format_report(offline)
    assert "未测" not in format_report(measured)
    assert "model a/b" in format_report(measured)
