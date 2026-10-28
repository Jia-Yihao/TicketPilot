"""Numbers in the sentence must already exist in the facts.

The expression model may phrase the answer, never change it. The guard is
deliberately blunt: every Arabic number (and every counted Chinese numeral)
is looked up in the server-computed set; one miss discards the sentence.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.sql.narrative import FactualityGuard, fact_numbers, narrate
from app.sql.snapshot import AnalyticsSnapshot, FactGroup, RecentFact

NOW = datetime(2026, 10, 23, 12, 0, 0)


def _snapshot(**overrides):  # type: ignore[no-untyped-def]
    values = {
        "query_id": "qry_guard",
        "dataset": "refunds",
        "label": "退款",
        "unit": "笔",
        "metric": "count_and_sum",
        "group_by": None,
        "time_range": "上个月",
        "count": 3,
        "total_cents": 12900,
        "groups": (),
        "recent": (
            RecentFact(
                at="2026-09-20",
                status="已到账",
                amount_cents=12900,
                reference="RF-1001",
            ),
        ),
        "rows": ({"id": "1"},),
        "total_rows": 3,
        "truncated": False,
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=15),
    }
    values.update(overrides)
    return AnalyticsSnapshot(**values)


def _count(overrides=None):  # type: ignore[no-untyped-def]
    values = {
        "metric": "count",
        "total_cents": 0,
        "group_by": None,
        "groups": (),
        "recent": (),
        "rows": ({"id": "1"}, {"id": "2"}),
        "total_rows": 2,
    }
    values.update(overrides or {})
    return _snapshot(**values)


class _Reply:
    model_name = "stub"

    def __init__(self, text: str) -> None:
        self.text = text

    def narrate(self, prompt):  # type: ignore[no-untyped-def]
        from app.sql.narrative import NarrationCall

        return self.text, NarrationCall(requests=1)


def test_the_true_numbers_pass() -> None:
    verdict = FactualityGuard().check(
        "上个月共有 3 笔退款，合计 129.00 元，最近一笔是 129.00 元。",
        _snapshot(),
    )
    assert verdict.ok is True
    assert verdict.missing == ()


def test_changing_an_amount_is_caught() -> None:
    verdict = FactualityGuard().check(
        "上个月共有 3 笔退款，合计 130.00 元。", _snapshot()
    )
    assert verdict.ok is False
    assert "130.00" in verdict.missing


def test_inventing_an_amount_is_caught() -> None:
    verdict = FactualityGuard().check(
        "上个月共有 3 笔退款，合计 129.00 元，另有 88.00 元待处理。",
        _snapshot(),
    )
    assert verdict.ok is False
    assert "88.00" in verdict.missing


def test_changing_the_count_is_caught() -> None:
    verdict = FactualityGuard().check("上个月共有 5 笔退款。", _count())
    assert verdict.ok is False
    assert "5" in verdict.missing


def test_a_chinese_count_must_match_too() -> None:
    verdict = FactualityGuard().check("上个月共有三笔退款。", _count())
    assert verdict.ok is True
    changed = FactualityGuard().check("上个月共有五笔退款。", _count())
    assert changed.ok is False
    assert "五" in changed.missing


def test_thousand_separators_are_normalised() -> None:
    verdict = FactualityGuard().check(
        "合计 12,900.00 元。",
        _snapshot(total_cents=1290000, metric="sum"),
    )
    assert verdict.ok is True


def test_a_date_from_the_facts_is_allowed() -> None:
    verdict = FactualityGuard().check(
        "最近一笔发生在 2026-09-20。", _snapshot()
    )
    assert verdict.ok is True


def test_a_group_number_is_allowed() -> None:
    snapshot = _snapshot(
        metric="count",
        total_cents=0,
        group_by="status",
        groups=(
            FactGroup(bucket="SUCCEEDED", label="已到账", count=2, total_cents=0),
            FactGroup(bucket="FAILED", label="退款失败", count=1, total_cents=0),
        ),
        recent=(),
        total_rows=3,
    )
    assert FactualityGuard().check("已到账 2 笔，退款失败 1 笔。", snapshot).ok is True
    assert FactualityGuard().check("已到账 4 笔。", snapshot).ok is False


def test_the_fact_set_names_every_number_it_knows() -> None:
    numbers = fact_numbers(_snapshot())
    assert "3" in numbers
    assert "129.00" in numbers
    assert "2026-09-20" in numbers
    assert "RF-1001" not in numbers


def test_a_failed_check_discards_the_sentence() -> None:
    result = narrate(
        "我上个月退了几笔款，多少钱",
        _snapshot(),
        _Reply("上个月共有 4 笔退款，合计 200.00 元。"),
    )
    assert result.source == "template"
    assert result.faithfulness == "failed"
    assert "4" in result.missing
    assert "200.00" in result.missing
    assert "3" in result.reply


def test_the_facts_keep_the_exact_cents() -> None:
    snapshot = _snapshot(total_cents=12901, metric="sum", recent=())
    assert FactualityGuard().check("合计 129.01 元。", snapshot).ok is True
    assert FactualityGuard().check("合计 129.00 元。", snapshot).ok is False


@pytest.mark.parametrize(
    "text",
    [
        "上个月共有 3 笔退款，合计 129.00 元。",
        "3 笔退款，金额 129.00 元。",
    ],
)
def test_harmless_phrasings_pass(text: str) -> None:
    assert FactualityGuard().check(text, _snapshot()).ok is True


@pytest.mark.parametrize("sign", ["-", "\u2212", "\uff0d", "负"])
def test_a_negated_amount_is_caught(sign: str) -> None:
    verdict = FactualityGuard().check(
        f"上个月共有 3 笔退款，合计 {sign}129.00 元。", _snapshot()
    )
    assert verdict.ok is False
    assert f"{sign}129.00" in verdict.missing


def test_digits_glued_to_letters_are_still_checked() -> None:
    snapshot = _snapshot(
        recent=(
            RecentFact(
                at="2026-09-20",
                status="已到账",
                amount_cents=12900,
                reference="DE20010003",
            ),
        )
    )
    assert (
        FactualityGuard().check("单号 DE20010003 已到账。", snapshot).ok is True
    )
    changed = FactualityGuard().check("单号 DE20019999 已到账。", snapshot)
    assert changed.ok is False
    assert "20019999" in changed.missing


def test_a_date_moved_by_one_day_is_caught() -> None:
    snapshot = _snapshot(
        recent=(
            RecentFact(
                at="2026-09-20",
                status="已到账",
                amount_cents=12900,
                reference="RF-1001",
            ),
            RecentFact(
                at="2026-10-21",
                status="已到账",
                amount_cents=5000,
                reference="RF-1002",
            ),
        )
    )
    verdict = FactualityGuard().check(
        "最近一笔发生在 2026-10-20。", snapshot
    )
    assert verdict.ok is False
    assert "2026-10-20" in verdict.missing


@pytest.mark.parametrize(
    "text", ["2026年9月3日", "9月3日", "9月3号"]
)
def test_a_chinese_date_on_another_day_is_caught(text: str) -> None:
    verdict = FactualityGuard().check(f"最近一笔发生在 {text}。", _snapshot())
    assert verdict.ok is False
    assert text in verdict.missing


@pytest.mark.parametrize(
    "text", ["2026年9月20日", "9月20日", "9月20号", "2026/9/20"]
)
def test_a_chinese_date_from_the_facts_is_allowed(text: str) -> None:
    verdict = FactualityGuard().check(f"最近一笔发生在 {text}。", _snapshot())
    assert verdict.ok is True, verdict.missing


@pytest.mark.parametrize("token", ["20", "2026", "9"])
def test_a_date_component_is_not_a_count(token: str) -> None:
    """The day, year and month of a recent row are not counts of refunds."""
    verdict = FactualityGuard().check(
        f"上个月共有 {token} 笔退款。", _snapshot()
    )
    assert verdict.ok is False
    assert token in verdict.missing


def _count_50():
    return _snapshot(
        metric="count",
        count=50,
        total_cents=0,
        rows=({"id": "1"},),
        total_rows=50,
        recent=(),
    )


@pytest.mark.parametrize(
    ("text", "token"),
    [
        ("占比 50%。", "50%"),
        ("占比 50 %。", "50 %"),
        ("占比 50％。", "50％"),
        ("占比 50﹪。", "50﹪"),
        ("占比 50٪。", "50٪"),
        ("占比百分之五十。", "百分之五十"),
        ("单号 V50 % 的退款。", "50 %"),
    ],
)
def test_a_made_up_percentage_is_caught(text: str, token: str) -> None:
    verdict = FactualityGuard().check(text, _count_50())
    assert verdict.ok is False
    assert token in verdict.missing


def test_the_count_itself_is_still_allowed() -> None:
    assert FactualityGuard().check("共有 50 笔。", _count_50()).ok is True


def test_a_fullwidth_rate_discards_the_model_sentence() -> None:
    result = narrate(
        "上个月退了几笔款，占比多少",
        _count_50(),
        _Reply("上个月共有 50 笔退款，占比 50％。"),
    )
    assert result.source == "template"
    assert result.faithfulness == "failed"
    assert "50％" in result.missing


@pytest.mark.parametrize(
    "phrase",
    [
        "最近一笔",
        "最近两笔",
        "第一笔",
        "下一笔",
        "这一笔",
        "那一笔",
        "前一笔",
        "另一笔",
        "同一笔",
        "每一笔",
    ],
)
def test_a_determiner_prefix_is_not_a_count(phrase: str) -> None:
    """A determiner names a row; only its own "一 / 两" is not a claim."""
    snapshot = _snapshot(
        count=7,
        rows=tuple({"id": str(index)} for index in range(7)),
        total_rows=7,
        recent=(
            RecentFact(
                at="2026-09-19",
                status="已到账",
                amount_cents=5000,
                reference="RF-1002",
            ),
            RecentFact(
                at="2026-09-20",
                status="已到账",
                amount_cents=12900,
                reference="RF-1001",
            ),
            RecentFact(
                at="2026-09-18",
                status="已到账",
                amount_cents=3000,
                reference="RF-1003",
            ),
        ),
    )
    facts = fact_numbers(snapshot)
    assert "1" not in facts and "一" not in facts
    assert "2" not in facts and "两" not in facts
    verdict = FactualityGuard().check(f"上个月{phrase}退款。", snapshot)
    assert verdict.ok is True, verdict.missing


def test_a_determiner_only_excuses_one_and_two() -> None:
    """「前五笔」 and 「只有一笔」 are counts, not determiners."""
    snapshot = _snapshot(
        count=7,
        rows=tuple({"id": str(index)} for index in range(7)),
        total_rows=7,
        recent=(),
    )
    for text in (
        "上个月共有五笔退款。",
        "上个月只有一笔退款。",
        "上个月前五笔退款。",
        "上个月另五笔退款。",
        "上个月共有两笔退款。",
    ):
        verdict = FactualityGuard().check(text, snapshot)
        assert verdict.ok is False, text
        assert verdict.missing, text
