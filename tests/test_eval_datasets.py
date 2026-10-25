from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agent.intent_catalog import get_catalog
from app.core.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
_KINDS = {"normal", "boundary", "failure"}


def _dataset(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "evals" / name).read_text(encoding="utf-8"))


def test_golden_set_shape() -> None:
    dataset = _dataset("golden_set.json")
    cases = dataset["cases"]
    assert len(cases) >= 40
    assert len({case["id"] for case in cases}) == len(cases)
    required = {
        "id",
        "tenant",
        "user",
        "request",
        "kind",
        "expected_intent",
        "expected_outcome",
        "expected_tool",
        "expect",
    }
    known = set(get_catalog().specs)
    covered: set[str] = set()
    for case in cases:
        assert required <= set(case), case["id"]
        assert case["kind"] in _KINDS, case["id"]
        assert case["expected_outcome"], case["id"]
        assert case["expected_tool"] is None or isinstance(case["expected_tool"], str)
        assert case["expected_intent"] in known, case["id"]
        covered.add(case["expected_intent"])
    assert len({case["kind"] for case in cases}) == len(_KINDS)
    declared = set(dataset["coverage"]["task_intents"]) | set(
        dataset["coverage"]["support_only_intents"]
    )
    assert declared == known
    assert covered == set(dataset["coverage"]["task_intents"])


def test_golden_tools_are_registered(tmp_path: Path) -> None:
    application = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+pysqlite:///{tmp_path / 'registry.db'}",
            auto_create_tables=True,
        )
    )
    try:
        with application.state.session_factory() as session:
            registry = application.state.runtime._services(session).gateway.registry
            registered = {manifest.name for manifest in registry.manifests()}
        expected = {
            case["expected_tool"]
            for case in _dataset("golden_set.json")["cases"]
            if case["expected_tool"] is not None
        }
        assert expected <= registered
    finally:
        application.state.engine.dispose()


def test_support_set_shape() -> None:
    dataset = _dataset("support_set.json")
    cases = dataset["cases"]
    assert len(cases) >= 60
    assert len({case["id"] for case in cases}) == len(cases)
    known = set(get_catalog().specs)
    covered: set[str] = set()
    for case in cases:
        assert case["kind"] in _KINDS, case["id"]
        turns = case["turns"]
        assert turns, case["id"]
        assert turns[0]["message"] == case["query"], case["id"]
        for turn in turns:
            intent = turn["expect"]["intent"]
            assert intent in known, f"{case['id']}: unknown intent {intent}"
            covered.add(intent)
    assert covered == known
    assert len({case["kind"] for case in cases}) == len(_KINDS)


def test_support_clarifications_are_resumable() -> None:
    cases = _dataset("support_set.json")["cases"]
    known = set(get_catalog().specs)
    clarifications = [
        (case["id"], turn["expect"]["clarification"])
        for case in cases
        for turn in case["turns"]
        if turn["expect"].get("clarification")
    ]
    assert clarifications
    for case_id, clarification in clarifications:
        assert clarification["reason"], case_id
        assert clarification["expected_slot"], case_id
        assert clarification["resumable_intent"] in known, case_id
    resumed = [
        case["id"]
        for case in cases
        if any(
            turn["expect"].get("span_attrs", {}).get("support.classify", {}).get("resumed")
            for turn in case["turns"]
        )
    ]
    assert resumed, "no case follows a clarification to the resumed turn"
