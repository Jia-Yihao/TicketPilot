from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.core.output_guard import UNGROUNDED_MARKER, assertion_spans, guard_output
from evals.answer_metrics import (
    DEFAULT_THRESHOLDS,
    _evaluate,
    _failures,
    _gate_failures,
    _report,
    _run_dataset,
    _structured_numbers,
    normalize_span,
    number_spans,
    probe_guard,
    validate_dataset,
    whitelist_numbers,
)

ANSWER_SET = Path(__file__).resolve().parent.parent / "evals" / "answer_set.json"


def _case(**overrides: Any) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": "case-1",
        "question": "退款需要满足什么条件？",
        "policy_id": "POLICY-REFUND",
        "sections": ["退款条件"],
        "forbidden": ["15 天"],
    }
    case.update(overrides)
    return case


def _probe() -> dict[str, int]:
    return {"probe_replaced": 5, "probe_total": 5, "probe_grounded_untouched": 5}


def test_assertion_spans_returns_sorted_non_overlapping_slices() -> None:
    assert assertion_spans("") == ()

    text = "签收后 15 天内退款 129 元，成功率 99%，我们保证一定到账。"
    spans = assertion_spans(text)
    assert [kind for kind, _, _ in spans] == [
        "timeline",
        "money",
        "percentage",
        "commitment",
    ]
    cursor = 0
    for _, start, end in spans:
        assert cursor <= start < end <= len(text)
        assert text[start:end].strip()
        cursor = end


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("这笔退款一共 129 元，已经处理好了。", "money"),
        ("签收后 15 天内提交申请都可以。", "timeline"),
        ("退款成功率达到 99% 以上。", "percentage"),
        ("我们保证一定退款，请放心。", "commitment"),
        ("明天就会到账。", "immediate"),
    ],
)
def test_assertion_spans_reports_each_kind(sentence: str, expected: str) -> None:
    assert [kind for kind, _, _ in assertion_spans(sentence)] == [expected]


def test_assertion_spans_merge_keeps_the_kind_seen_by_guard() -> None:
    text = "我们保证 3 天内退款 999 元，请放心。"
    # The timeline sits inside the commitment span, so it disappears from the
    # merged spans but must still be reported by the guard's `kinds`.
    assert [kind for kind, _, _ in assertion_spans(text)] == ["commitment", "money"]
    result = guard_output(text, grounded=False)
    assert result.replaced == 2
    assert result.kinds == ("commitment", "money", "timeline")


def test_guard_output_counts_one_replacement_per_span() -> None:
    sentences = [
        "这笔退款一共 129 元，已经处理好了。",
        "签收后 15 天内提交申请都可以。",
        "退款成功率达到 99% 以上。",
        "我们保证一定退款，请放心。",
        "明天就会到账。",
    ]
    for sentence in sentences:
        result = guard_output(sentence, grounded=False)
        assert result.replaced == len(assertion_spans(sentence)) == 1
        assert UNGROUNDED_MARKER in result.reply

        grounded = guard_output(sentence, grounded=True)
        assert grounded.replaced == 0
        assert grounded.kinds == ()
        assert grounded.reply == sentence


@pytest.mark.parametrize(
    ("kind", "text", "expected"),
    [
        ("timeline", "七天内", (Decimal(7), "天")),
        ("timeline", "两个工作日内", (Decimal(2), "工作日")),
        ("timeline", "三十天内", (Decimal(30), "天")),
        ("timeline", "15 日", (Decimal(15), "天")),
        ("money", "129 元", (Decimal(129), "元")),
        ("money", "129.50 元", (Decimal("129.50"), "元")),
        ("money", "1,299 元", (Decimal(1299), "元")),
        ("money", "129,50 元", (Decimal("129.50"), "元")),
        ("percentage", "99%", (Decimal(99), "%")),
    ],
)
def test_normalize_span(kind: str, text: str, expected: tuple[Decimal, str]) -> None:
    assert normalize_span(kind, text) == expected


def test_number_spans_skips_wording_assertions() -> None:
    assert number_spans("我们保证一定退款，请放心。") == []
    assert number_spans("明天就会到账。") == []
    assert number_spans("退款成功率百分之百") == []


def test_whitelist_numbers_and_structured_sources() -> None:
    assert whitelist_numbers("签收后 30 天内提出退款申请。") == {
        (Decimal(30), "天")
    }

    found: set[tuple[Decimal, str]] = set()
    _structured_numbers(
        {
            "total_cents": 12900,
            "count": 15,
            "paid_cents": True,
            "nested": [{"refundable_cents": 500}],
        },
        found,
    )
    # Only *_cents money is a source; bare counts must not vouch for numbers.
    assert found == {(Decimal(129), "元"), (Decimal(5), "元")}


def test_validate_dataset_accepts_a_well_formed_case() -> None:
    assert validate_dataset({"cases": [_case()]}) == [
        "dataset: expected at least 50 cases, found 1"
    ]


def test_validate_dataset_reports_each_shape_problem() -> None:
    dataset = {
        "cases": [
            _case(),
            _case(id="dup"),
            _case(id="dup"),
            _case(
                id="copied",
                question="已签收订单应在签收后 30 天内提出退款申请。",
            ),
            _case(id="unknown-policy", policy_id="refund-v2"),
            _case(id="unknown-section", sections=["不存在的章节"]),
            _case(id="no-forbidden", forbidden=[]),
            _case(id="empty-question", question=""),
        ]
    }
    failures = validate_dataset(dataset)

    assert any("duplicate case id" in item for item in failures)
    assert any("copies policy text" in item for item in failures)
    assert any("unknown policy_id 'refund-v2'" in item for item in failures)
    assert any(
        "section '不存在的章节' does not exist in refund-v3.md" in item
        for item in failures
    )
    assert any("empty forbidden list" in item for item in failures)
    assert any("empty question" in item for item in failures)
    assert "dataset: expected at least 50 cases, found 8" in failures
    assert not any(item.startswith("case-1:") for item in failures)


def test_probe_guard_covers_every_kind() -> None:
    assert probe_guard() == {
        "probe_replaced": 5,
        "probe_total": 5,
        "probe_grounded_untouched": 5,
    }


def test_report_prints_the_four_criteria_separately(capsys: pytest.CaptureFixture[str]) -> None:
    readings: list[dict[str, Any]] = [
        {
            "id": "a",
            "hit": True,
            "no_evidence": False,
            "fabricated": [],
            "untraceable": [],
            "guard_replacements": 0,
        },
        {
            "id": "b",
            "hit": False,
            "no_evidence": True,
            "fabricated": ["15 天"],
            "untraceable": ["'15 天' -> (15, 天)"],
            "guard_replacements": 1,
        },
    ]
    counts = _report(readings, DEFAULT_THRESHOLDS, _probe())
    out = capsys.readouterr().out

    assert "answer cases: 2" in out
    assert "citation_hit: 1/2 = 0.5000 (threshold 0.9)  no_evidence: 1" in out
    assert "fabrication: 1/2 = 0.5000 (threshold 0.0)" in out
    assert "untraceable_number: 1/2 = 0.5000 (threshold 0.0)" in out
    assert "guard.corpus_turns: 2  guard.corpus_replaced: 1" in out
    assert "guard.probe_replaced: 5/5  probe_grounded_untouched: 5/5" in out
    assert "structurally 0" in out
    assert "different denominators" in out
    assert counts["citation_hit"] == 1
    assert counts["no_evidence"] == 1
    assert counts["fabrication"] == 1
    assert counts["untraceable"] == 1
    assert counts["guard_replaced"] == 1


def test_failures_list_every_violation() -> None:
    readings: list[dict[str, Any]] = [
        {
            "id": "a",
            "hit": True,
            "no_evidence": False,
            "fabricated": [],
            "untraceable": [],
            "status": 200,
        },
        {
            "id": "b",
            "hit": False,
            "no_evidence": True,
            "fabricated": ["15 天"],
            "untraceable": ["'15 天' -> (15, 天)"],
            "status": 500,
        },
    ]
    assert _failures(readings) == [
        "b: citation_miss (no evidence retrieved)",
        "b: fabricated ['15 天']",
        "b: untraceable [\"'15 天' -> (15, 天)\"]",
        "b: http 500",
    ]


def test_run_dataset_partial_single_case(tmp_path: Path) -> None:
    dataset = json.loads(ANSWER_SET.read_text(encoding="utf-8"))
    single = {
        "thresholds": dataset["thresholds"],
        "cases": [dataset["cases"][0]],
    }
    url = f"sqlite+pysqlite:///{(tmp_path / 'answer.db').as_posix()}"
    assert _run_dataset(single, url, partial=True, enforce=True) == 0


def test_run_dataset_enforce_gate(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{(tmp_path / 'answer.db').as_posix()}"

    assert _run_dataset({"cases": []}, url, partial=True, enforce=True) == 2

    miss = {
        "cases": [
            _case(id="miss", question="你好", sections=["退款条件"]),
        ]
    }
    assert _run_dataset(miss, url, partial=True, enforce=True) == 1


# ------------------------------------------- end-to-end through the evaluator
# A stub client, so `_evaluate` can be driven without a database or the HTTP
# app. The three bodies below are the minimum the evaluator reads: a reply with
# two numbers (one tractable, one not), a trace with a citation span and a
# guard span, and the cited section's own text.
_PROBE_CASE: dict[str, Any] = {
    "id": "probe-untraceable",
    "question": "签收之后多久内可以退款？",
    "policy_id": "POLICY-REFUND",
    "sections": ["退款条件"],
    "forbidden": ["15 天"],
}

_PROBE_BODY: dict[str, Any] = {
    # 30 天 is repeated by the cited section, 15 天 is stated by the reply only.
    "reply": "签收后 30 天内可以申请退款，退款 15 天到账。",
    "trace_id": "tr-1",
    "clarification": None,
    "context_cards": [
        {
            "kind": "policy",
            "card_id": "POLICY-REFUND",
            "fields": {"matched_section": "退款条件"},
            "sections": [{"id": "s1", "title": "退款条件"}],
        }
    ],
    "task": None,
}

_PROBE_TRACE: dict[str, Any] = {
    "spans": [
        {
            "kind": "retrieve",
            "name": "policy.search",
            "attributes": {
                "hits": 2,
                "citations": [
                    {
                        "document_id": "POLICY-REFUND",
                        "version": "v3",
                        "section": "退款条件",
                    }
                ],
            },
        },
        {
            "kind": "guard",
            "name": "output.grounded",
            "attributes": {"replacements": 0},
        },
    ]
}

_PROBE_SECTION: dict[str, Any] = {
    "section": {"content": "签收后 30 天内提出退款申请。"}
}

_PROBE_HEADERS: dict[str, str] = {"X-Tenant-Id": "demo", "X-User-Id": "alice"}


class _StubResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload


class _StubClient:
    """Minimal stand-in for ``TestClient``: fixed POST, routed GET."""

    def __init__(self, status_code: int = 200) -> None:
        self._status_code = status_code

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
    ) -> _StubResponse:
        assert url == "/api/v1/support/messages"
        return _StubResponse(_PROBE_BODY, self._status_code)

    def get(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> _StubResponse:
        if "/traces/" in url:
            return _StubResponse(_PROBE_TRACE)
        if "/sections/" in url:
            return _StubResponse(_PROBE_SECTION)
        return _StubResponse({"detail": "Not Found"}, status_code=404)


def test_evaluate_flags_untraceable_number_and_fabrication() -> None:
    reading = _evaluate(_StubClient(), _PROBE_HEADERS, _PROBE_CASE)

    assert reading["hit"] is True
    assert reading["no_evidence"] is False
    # The cited section repeats 30 天, so the white list vouches for it. 15 天
    # appears nowhere but in the reply: if the white list ever degrades to the
    # empty set, 30 天 shows up here too and this equality breaks.
    assert reading["untraceable"] == ["'15 天' -> (15, 天)"]
    assert reading["fabricated"] == ["15 天"]
    assert _failures([reading]) == [
        "probe-untraceable: fabricated ['15 天']",
        "probe-untraceable: untraceable [\"'15 天' -> (15, 天)\"]",
    ]


def test_evaluate_keeps_the_http_status() -> None:
    reading = _evaluate(_StubClient(status_code=500), _PROBE_HEADERS, _PROBE_CASE)

    assert reading["status"] == 500
    assert "probe-untraceable: http 500" in _failures([reading])


# --------------------------------------------------- the gate, not the misses
def _counts(**overrides: Any) -> dict[str, Any]:
    """The shape ``_report`` returns, with every reading passing."""
    counts: dict[str, Any] = {
        "cases": 60,
        "citation_hit": 60,
        "no_evidence": 0,
        "fabrication": 0,
        "untraceable": 0,
        "guard_turns": 60,
        "guard_replaced": 0,
        **_probe(),
    }
    counts.update(overrides)
    return counts


def test_gate_failures_compares_the_declared_rates() -> None:
    thresholds = DEFAULT_THRESHOLDS
    probe = _probe()

    # 55/60 = 0.9167 and 54/60 = 0.9000 are inside the 0.9 threshold, so the
    # threshold is live config rather than a value that never gets consulted.
    assert _gate_failures(_counts(citation_hit=55), thresholds, probe, 0) == []
    assert _gate_failures(_counts(citation_hit=54), thresholds, probe, 0) == []

    # 53/60 = 0.8833 is below it.
    below = _gate_failures(_counts(citation_hit=53), thresholds, probe, 0)
    assert len(below) == 1
    assert "citation_hit" in below[0]


def test_gate_failures_treats_zero_cases_as_a_failure() -> None:
    failures = _gate_failures(
        _counts(cases=0, citation_hit=0), DEFAULT_THRESHOLDS, _probe(), 0
    )

    assert len(failures) == 1
    assert "citation_hit" in failures[0]


def test_gate_failures_flags_each_other_breach() -> None:
    thresholds = DEFAULT_THRESHOLDS
    probe = _probe()

    fabrication = _gate_failures(_counts(fabrication=1), thresholds, probe, 0)
    assert len(fabrication) == 1
    assert "fabrication" in fabrication[0]

    untraceable = _gate_failures(_counts(untraceable=1), thresholds, probe, 0)
    assert len(untraceable) == 1
    assert "untraceable_number" in untraceable[0]

    http = _gate_failures(_counts(), thresholds, probe, 1)
    assert len(http) == 1
    assert "http_errors" in http[0]

    not_replaced = _gate_failures(
        _counts(), thresholds, {**probe, "probe_replaced": 4}, 0
    )
    assert not_replaced == ["guard probe did not replace all five unsourced sentences"]

    over_reach = _gate_failures(
        _counts(), thresholds, {**probe, "probe_grounded_untouched": 4}, 0
    )
    assert over_reach == ["guard probe touched a grounded sentence"]


def test_gate_failures_is_empty_when_every_reading_passes() -> None:
    assert _gate_failures(_counts(), DEFAULT_THRESHOLDS, _probe(), 0) == []
