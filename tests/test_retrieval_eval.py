from __future__ import annotations

import json
from pathlib import Path

from app.db.session import build_engine, build_session_factory, create_all
from app.seed import seed_all
from evals.retrieval_metrics import check_thresholds, evaluate

DATASET_PATH = Path(__file__).resolve().parent.parent / "evals" / "support_set.json"


def _report(tmp_path: Path):
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'retrieval-eval.db'}")
    create_all(engine)
    session_factory = build_session_factory(engine)
    seed_all(session_factory)
    try:
        report = evaluate(dataset, session_factory)
    finally:
        engine.dispose()
    return dataset, report


def test_retrieval_baseline_meets_thresholds(tmp_path: Path) -> None:
    dataset, report = _report(tmp_path)
    failures = check_thresholds(report, dataset.get("thresholds", {}))
    assert not failures, failures


def test_baseline_covers_every_policy_document(tmp_path: Path) -> None:
    dataset, report = _report(tmp_path)
    labelled = {
        case["policy_id"] for case in dataset["cases"] if case.get("policy_id")
    }
    assert labelled == {
        "POLICY-REFUND",
        "POLICY-DELAY",
        "POLICY-RETURN",
        "POLICY-TICKET",
    }


def test_expired_policy_version_is_never_retrieved(tmp_path: Path) -> None:
    _, report = _report(tmp_path)
    assert report.metrics["stale_hits"] == 0
    # The retired refund policy is version v2; it expired on 2024-12-31.
    for case in report.cases:
        assert all(not hit.endswith("@v2") for hit in case.retrieved)
