from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.session import build_engine, build_session_factory, create_all
from app.models.document import Document, DocumentChunk
from app.retrieval.rewrite import FACET_ORDER, SECTION_TITLES
from app.seed import seed_policies
from scripts.build_retrieval_set import build, render

ROOT = Path(__file__).resolve().parent.parent
DATASET_PATH = ROOT / "evals" / "retrieval_set.json"
GOLD_PATH = ROOT / "evals" / "retrieval" / "gold_cases.json"
SILVER_PATH = ROOT / "evals" / "retrieval" / "silver_templates.json"
EXTERNAL_DIR = ROOT / "evals" / "external"
EXTERNAL_SAMPLE = EXTERNAL_DIR / "ecommerce_customer_service_queries.jsonl"
EXTERNAL_MANIFEST = EXTERNAL_DIR / "manifest.json"

POLICY_IDS = {"POLICY-REFUND", "POLICY-DELAY", "POLICY-RETURN", "POLICY-TICKET"}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _dataset() -> dict[str, Any]:
    return _load(DATASET_PATH)


def _cases() -> list[dict[str, Any]]:
    return _dataset()["cases"]


def _external_rows() -> list[dict[str, Any]]:
    text = EXTERNAL_SAMPLE.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_generated_dataset_matches_its_sources() -> None:
    """The checked-in file is exactly what the generator produces."""
    actual = DATASET_PATH.read_text(encoding="utf-8")

    assert actual == render(build())


def test_generator_check_mode_returns_zero() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/build_retrieval_set.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_dataset_has_the_documented_shape() -> None:
    dataset = _dataset()

    assert dataset["version"] == 1
    assert dataset["k_values"] == [1, 3, 5]
    assert len(dataset["cases"]) == 1000
    assert dataset["gold_counts"]["total"] == 200
    assert dataset["silver_counts"]["total"] == 800
    assert set(dataset["thresholds"]) == {
        "gold_test",
        "stale_hits",
        "cross_tenant_hits",
    }


def test_case_ids_and_queries_are_unique() -> None:
    cases = _cases()

    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["query"] for case in cases}) == len(cases)


def test_every_case_satisfies_the_dataset_contract() -> None:
    cases = _cases()

    for case in cases:
        assert case["policy_id"] in POLICY_IDS, case["id"]
        assert case["sections"], case["id"]
        assert case["sections"][0] in SECTION_TITLES[case["policy_id"]], case["id"]
        assert case["facets"], case["id"]
        assert set(case["facets"]) <= set(FACET_ORDER), case["id"]
        assert case["label_quality"] in {"gold", "silver"}, case["id"]
        assert case["source"] in {"authored", "template"}, case["id"]
        assert case["split"] in {"dev", "test"}, case["id"]
        assert 4 <= len(case["query"]) <= 60, case["id"]
        if case["label_quality"] == "gold":
            assert case["source"] == "authored", case["id"]
            assert "template_id" not in case, case["id"]
        else:
            assert case["source"] == "template", case["id"]
            assert case["template_id"], case["id"]


def test_gold_and_silver_splits_are_fixed() -> None:
    cases = _cases()
    splits = Counter(
        (case["label_quality"], case["split"]) for case in cases
    )

    assert splits[("gold", "dev")] == 100
    assert splits[("gold", "test")] == 100
    assert splits[("silver", "dev")] == 400
    assert splits[("silver", "test")] == 400


def test_gold_cases_cover_every_section_and_facet() -> None:
    gold = [case for case in _cases() if case["label_quality"] == "gold"]
    sections = {case["sections"][0] for case in gold}
    facets = {facet for case in gold for facet in case["facets"]}

    assert sections == {title for titles in SECTION_TITLES.values() for title in titles}
    assert facets == set(FACET_ORDER)


def test_gold_queries_are_not_copied_from_the_policy_text() -> None:
    policy_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "app" / "resources" / "policies").glob("*.md"))
    )
    gold = [case for case in _cases() if case["label_quality"] == "gold"]

    for case in gold:
        assert case["query"] not in policy_text, case["id"]


def test_silver_cases_form_the_full_template_grid() -> None:
    source = _load(SILVER_PATH)
    silver = [case for case in _cases() if case["label_quality"] == "silver"]
    per_template = Counter(case["template_id"] for case in silver)
    declared = {template["template_id"] for template in source["templates"]}

    assert declared == set(per_template)
    for template in source["templates"]:
        assert per_template[template["template_id"]] == 40, template["template_id"]
        assert len(template["patterns"]) == 8, template["template_id"]
        assert len(source["slot_sets"][template["slots"]]) == 5, template["template_id"]


def test_thresholds_only_gate_the_labelled_layers() -> None:
    thresholds = _dataset()["thresholds"]

    assert set(thresholds["gold_test"]) == {
        "recall@1",
        "recall@3",
        "recall@5",
        "mrr",
        "ndcg@5",
    }
    assert thresholds["stale_hits"] == 0
    assert thresholds["cross_tenant_hits"] == 0
    # Silver layers report coverage; they must never carry a pass/fail gate.
    assert "silver_dev" not in thresholds
    assert "silver_test" not in thresholds


def test_silver_labels_are_declared_as_silver_in_the_source() -> None:
    source = _load(SILVER_PATH)

    assert "银标" in source["description"]
    assert "外部数据的问答/意图标签一律不采用" in source["description"]


def test_dataset_sections_exist_in_the_active_policy_chunks(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'policy-sections.db'}")
    create_all(engine)
    factory = build_session_factory(engine)
    try:
        with factory() as session:
            seed_policies(session)
            session.commit()
            rows = session.execute(
                select(
                    Document.meta,
                    Document.effective_to,
                    DocumentChunk.section,
                )
                .join(DocumentChunk, DocumentChunk.document_id == Document.id)
                .where(Document.status == "ACTIVE")
            ).all()
    finally:
        engine.dispose()

    today = date.today()
    active: dict[str, set[str]] = {}
    for meta, effective_to, section in rows:
        if not section or (effective_to is not None and effective_to < today):
            continue
        policy_id = meta.get("policy_id")
        if policy_id in POLICY_IDS:
            active.setdefault(policy_id, set()).add(section)

    assert active == {policy_id: set(titles) for policy_id, titles in SECTION_TITLES.items()}


def test_external_sample_is_phrasing_only() -> None:
    manifest = _load(EXTERNAL_MANIFEST)
    rows = _external_rows()
    sample_bytes = EXTERNAL_SAMPLE.read_bytes()

    assert manifest["license"] == "Apache-2.0"
    assert manifest["dataset_url"].startswith("https://www.modelscope.cn/datasets/")
    assert manifest["sample_rows"] == len(rows) == 200
    assert hashlib.sha256(sample_bytes).hexdigest() == manifest["sample_sha256"]
    assert "phrasing source only" in manifest["used_for"]

    previous = 0
    for row in rows:
        provenance = row["provenance"]
        assert provenance["dataset"] == manifest["dataset"]
        assert provenance["file"] == manifest["source_file"]
        assert provenance["line"] > previous
        previous = provenance["line"]
        assert row["query"].strip()
        # The external file carries a user utterance and its location only:
        # no answer, intent or label from the external dataset is imported.
        assert set(row) == {"provenance", "query"}

    dataset_queries = {case["query"] for case in _cases()}
    assert not dataset_queries & {row["query"] for row in rows}
    assert {case["source"] for case in _cases()} == {"authored", "template"}
