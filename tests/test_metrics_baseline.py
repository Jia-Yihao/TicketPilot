"""Pins the metric baseline gate.

The gate exists because an eval change used to be judged by a printed table
nobody could diff: the numbers landed in ``.tmp/`` and no run could be compared
with the one before it. These tests pin the three rules that make the gate
trustworthy — the comparison semantics (exact unless a tolerance is asked for),
the projection rules (numbers and booleans only, no per-case rows) and the
committed baseline files (no wall-clock reading may live inside one).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from evals.metrics_baseline import (
    BASELINE_DIR,
    SOURCES,
    VOLATILE,
    compare,
    flatten,
    is_volatile,
)


def test_compare_is_empty_for_identical_readings() -> None:
    readings = {"passed": 40, "total": 40, "failed_count": 0, "ok": True}
    baseline = {"_meta": {"source": "golden"}, "readings": dict(readings)}
    current = {"_meta": {"source": "golden"}, "readings": dict(readings)}
    assert compare(baseline, current) == []


def test_compare_reports_one_changed_number_with_its_key_and_both_values() -> None:
    baseline = {"readings": {"failed_count": 0, "passed": 40, "total": 40}}
    current = {"readings": {"failed_count": 0, "passed": 39, "total": 40}}
    problems = compare(baseline, current)
    assert len(problems) == 1
    assert "passed" in problems[0]
    assert "40" in problems[0]
    assert "39" in problems[0]


def test_compare_reports_a_missing_key_and_an_extra_key() -> None:
    baseline = {"readings": {"passed": 40, "total": 40}}
    current = {"readings": {"passed": 40, "total": 40, "failed_count": 1}}
    extra = compare(baseline, current)
    assert len(extra) == 1
    assert "failed_count" in extra[0]

    shorter = {"readings": {"passed": 40}}
    missing = compare(baseline, shorter)
    assert len(missing) == 1
    assert "total" in missing[0]


def test_tolerance_absorbs_small_drift_and_still_reports_the_rest() -> None:
    baseline = {"readings": {"mrr": 1.0}}
    within = {"readings": {"mrr": 1.2}}
    beyond = {"readings": {"mrr": 1.6}}

    assert compare(baseline, within, {"mrr": 0.5}) == []
    problems = compare(baseline, beyond, {"mrr": 0.5})
    assert len(problems) == 1
    assert "mrr" in problems[0]
    # 默认容差是 0：没有显式要求时任何漂移都算。
    assert compare(baseline, within) != []


def test_booleans_must_match_exactly_and_never_compare_as_numbers() -> None:
    baseline = {"readings": {"invariants.ok": True}}
    assert compare(baseline, {"readings": {"invariants.ok": 1.0}}, {"invariants.ok": 10}) != []
    assert compare(baseline, {"readings": {"invariants.ok": False}}) != []


def test_committed_baselines_load_and_carry_no_volatile_reading() -> None:
    for source in SOURCES:
        path = BASELINE_DIR / f"{source}.json"
        assert path.is_file(), f"缺少基线文件：{path.as_posix()}"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["_meta"]["source"] == source
        assert payload["_meta"]["command"], f"{source} 的基线没有记录生成命令"
        readings = payload["readings"]
        assert readings, f"{source} 的基线没有记录任何读数"
        for key in readings:
            assert not any(key == prefix or key.startswith(prefix + ".") for prefix in VOLATILE), (
                f"{source} 的基线里混进了易变读数：{key}"
            )
            assert not is_volatile(key), f"{source} 的基线里混进了易变读数：{key}"


def test_flatten_keeps_numbers_and_booleans_only() -> None:
    payload = {
        "passed": 40,
        "label": "golden",
        "absent": None,
        "nested": {"ratio": 0.5, "ok": True, "note": "x"},
        "rows": [{"count": 1}],
    }
    assert flatten(payload) == {"passed": 40, "nested.ratio": 0.5, "nested.ok": True}


def test_cli_without_a_mode_prints_help_and_exits_two() -> None:
    script = Path(__file__).resolve().parent.parent / "evals" / "metrics_baseline.py"
    completed = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 2
    assert "--record" in completed.stdout
    assert "--check" in completed.stdout
