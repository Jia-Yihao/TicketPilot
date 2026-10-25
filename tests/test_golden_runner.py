from __future__ import annotations

import json
from pathlib import Path

from evals.run import _run_dataset

DATASET_PATH = Path(__file__).resolve().parent.parent / "evals" / "golden_set.json"


def _dataset() -> dict:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def test_golden_runner_refuses_a_dirty_database(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'golden.db'}"
    assert _run_dataset(_dataset(), url) == 0
    # A second run would reuse the open approval slot and refund the same order,
    # so the runner must refuse instead of reporting per-case failures.
    assert _run_dataset(_dataset(), url) == 2
    assert _run_dataset(_dataset(), url, reset=True) == 0
