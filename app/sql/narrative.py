"""Talking about server-computed facts, without letting the model do maths.

The expression path has one job: turn an :class:`AnalyticsSnapshot` into a
sentence a customer can read. Three pieces keep it honest:

* **The prompt** carries the question plus L1 (the aggregate) and L2 (a few
  recent rows). L3 never leaves the snapshot, so the model cannot count rows
  that were not already counted.
* **The constraint** is a fixed sentence, not a template a caller may tune:
  no arithmetic, no new numbers, no database vocabulary, no tools.
* **The guard** extracts every number from the answer and looks it up in the
  fact set. A miss discards the whole sentence; the deterministic template
  answers instead. The card and the ledger keep the same facts either way.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from app.core.money import format_cents
from app.sql.schema import DATASETS, status_label
from app.sql.snapshot import AnalyticsSnapshot, estimate_tokens

__all__ = [
    "FACTUALITY_CONSTRAINT",
    "AnalyticsNarrator",
    "FactualityGuard",
    "FactualityResult",
    "NarrationCall",
    "NarrationPrompt",
    "NarrationResult",
    "build_prompt",
    "fact_numbers",
    "narrate",
    "render_template",
]


# The model is a narrator, not an analyst. Written once, never assembled from
# request data, so two runs on the same facts ask for the same thing.
FACTUALITY_CONSTRAINT = (
    "你是售后统计播报器。只能使用给定的事实：不得自行计算、换算、四舍五入、"
    "补充或省略任何数字；回复中的每个数字都必须逐字出现在事实里；"
    "不得提及数据库结构或查询语句；不得调用工具；"
    "事实不足以回答时，直接说明无法回答。"
)

# Arabic numbers, including thousands separators. The percent sign is part of
# the token so a made-up rate is rejected rather than silently yielding "50".
# All four percent signs a Chinese reply may use count, and a space before the
# sign is still the same claim.
# A sign only counts when it is not glued to an identifier: "-129.00" is a
# negative claim, while the hyphen in "ORD-DEMO-1001" is not. Both full-width
# and mathematical minus, plus the "负" prefix, mean the same thing.
_ARABIC_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_])[-−－+负]?\d+(?:[,\u00a0]\d{3})*(?:\.\d+)?(?:\s*[%％﹪٪])?"
)

# Digits glued to letters are still a claim: "DE20010003" must not become
# "DE20019999" without the guard noticing. This pass only runs on spans the
# signed pass did not already cover.
_PLAIN_DIGITS = re.compile(
    r"\d+(?:[,\u00a0]\d{3})*(?:\.\d+)?(?:\s*[%％﹪٪])?"
)

# A date is one fact. Extracting its parts would let "2026" pass while the
# sentence moved the answer to another day. ISO, slash, Chinese year-month-day
# and a bare month-day are all recognised; "号" is the spoken form of "日".
_DATE = re.compile(
    r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日号]?|\d{1,2}月\d{1,2}[日号]"
)

# "百分之五十" is a rate claim even though its numeral is not attached to a
# measure word; the whole phrase is one token, and facts never carry rates.
_CN_PERCENT = re.compile(
    r"百分之\s*[0-9零〇一二三四五六七八九十百千万两]+(?:\.\d+)?[%％﹪٪]?"
)

# A Chinese numeral only counts when it is attached to a measure word. Without
# that anchor, ordinary words ("统一", "一共", "一样") would look like numbers.
# A determiner ("最近一笔", "第一笔", "下一笔") says which row, not how many:
# reading its "一" as a count would reject a faithful sentence while the row
# count is not 1. Only "一 / 两" are determiners here; "前五笔" still counts.
_CN_NUMBER = re.compile(
    r"([零〇一二三四五六七八九十百千万两]+)"
    r"(?=\s*(?:笔|单|个|次|条|件|款|张|元|块|天|日|周|月))"
)

_CN_DETERMINER_PREFIXES = frozenset("第下这那前另同每")

_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


class AnalyticsNarrator(Protocol):
    """One model call that turns facts into words. It cannot change them."""

    model_name: str | None

    def narrate(
        self, prompt: NarrationPrompt
    ) -> tuple[str | None, NarrationCall]: ...


@dataclass(frozen=True, slots=True)
class NarrationCall:
    """What one expression call cost, in the units the usage ledger keeps."""

    requests: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    status: str = "OK"


@dataclass(frozen=True, slots=True)
class NarrationPrompt:
    """The exact text sent to the model, and what it cost to build."""

    system: str
    user: str
    text: str
    facts_chars: int
    chars: int
    estimated_tokens: int


@dataclass(frozen=True, slots=True)
class NarrationResult:
    """The sentence to show, and the evidence for where it came from."""

    reply: str
    source: str  # "model" | "template"
    prompt: NarrationPrompt
    call: NarrationCall | None = None
    faithfulness: str = "skipped"  # "ok" | "failed" | "skipped"
    numbers: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FactualityResult:
    ok: bool
    numbers: tuple[str, ...]
    missing: tuple[str, ...]


# --------------------------------------------------------------------- prompt
def build_prompt(question: str, snapshot: AnalyticsSnapshot) -> NarrationPrompt:
    """The question and the server facts — never the full row set."""
    context = snapshot.prompt_context()
    user = json.dumps(
        {"question": question, "facts": context["facts"]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    text = f"{FACTUALITY_CONSTRAINT}\n{user}"
    return NarrationPrompt(
        system=FACTUALITY_CONSTRAINT,
        user=user,
        text=text,
        facts_chars=int(context["chars"]),
        chars=len(text),
        estimated_tokens=estimate_tokens(text),
    )


# ------------------------------------------------------------------- template
def render_template(
    *,
    dataset: str,
    metric: str,
    group_by: str | None,
    span: str,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """The deterministic sentence and card fields, used by both paths.

    The controlled SQL path and the prefetch path share this renderer, so the
    same question produces the same words and the same card fields whether a
    model was involved or not.
    """
    spec = DATASETS[dataset]
    if group_by is None:
        record = rows[0] if rows else {}
        count = _as_int(record.get("metric"))
        total = _as_int(record.get("metric_total"))
        if metric == "sum":
            sentence = f"{span}你的{spec.label}金额合计 {format_cents(total)} 元。"
        elif metric == "count_and_sum":
            sentence = (
                f"{span}你有 {count} {spec.unit}{spec.label}记录，"
                f"金额合计 {format_cents(total)} 元。"
            )
        else:
            sentence = f"{span}你有 {count} {spec.unit}{spec.label}记录。"
        fields: dict[str, Any] = {"range": span, "currency": "CNY"}
        if metric in {"count", "count_and_sum"}:
            fields["records"] = count
        if metric in {"sum", "count_and_sum"}:
            fields["amount_cents"] = total
        return sentence, fields

    parts: list[str] = []
    fields = {"range": span}
    for row in rows:
        bucket = str(row.get("bucket", ""))
        count = _as_int(row.get("metric"))
        total = _as_int(row.get("metric_total"))
        label = (
            status_label(spec.name, bucket) if group_by == "status" else bucket
        )
        key = label or "unknown"
        if metric == "sum":
            parts.append(f"{label} {format_cents(total)} 元")
        elif metric == "count_and_sum":
            parts.append(f"{label} {count} {spec.unit}（{format_cents(total)} 元）")
        else:
            parts.append(f"{label} {count} {spec.unit}")
        if metric in {"count", "count_and_sum"}:
            fields[key] = f"{count} {spec.unit}"
        if metric in {"sum", "count_and_sum"}:
            fields[f"{key}_amount_cents"] = total
    sentence = (
        f"{span}你的{spec.label}分布：" + "、".join(parts) + "。"
        if parts
        else f"{span}没有{spec.label}记录。"
    )
    return sentence, fields


# ----------------------------------------------------------------- expression
def narrate(
    question: str,
    snapshot: AnalyticsSnapshot,
    narrator: AnalyticsNarrator | None,
) -> NarrationResult:
    """Ask the model to phrase the facts, or fall back to the template.

    ``narrator is None`` is the rules mode and the disabled switch: no model
    call is made, and the reply is byte-for-byte the template. A failed or
    unfaithful answer keeps the same facts and the same card; only the
    sentence changes.
    """
    prompt = build_prompt(question, snapshot)
    template, _fields = render_template(
        dataset=snapshot.dataset,
        metric=snapshot.metric,
        group_by=snapshot.group_by,
        span=snapshot.time_range,
        rows=snapshot.l1_rows(),
    )
    if narrator is None:
        return NarrationResult(reply=template, source="template", prompt=prompt)

    try:
        reply, call = narrator.narrate(prompt)
    except Exception:
        return NarrationResult(
            reply=template,
            source="template",
            prompt=prompt,
            call=NarrationCall(status="FAILED"),
        )
    if not reply or not reply.strip():
        return NarrationResult(
            reply=template, source="template", prompt=prompt, call=call
        )

    verdict = FactualityGuard().check(reply, snapshot)
    if not verdict.ok:
        return NarrationResult(
            reply=template,
            source="template",
            prompt=prompt,
            call=call,
            faithfulness="failed",
            numbers=verdict.numbers,
            missing=verdict.missing,
        )
    return NarrationResult(
        reply=reply.strip(),
        source="model",
        prompt=prompt,
        call=call,
        faithfulness="ok",
        numbers=verdict.numbers,
    )


# --------------------------------------------------------------------- guard
class FactualityGuard:
    """Every number in the sentence must exist in the server's fact set."""

    def check(
        self, text: str, snapshot: AnalyticsSnapshot
    ) -> FactualityResult:
        allowed = {_canonical(item) for item in fact_numbers(snapshot)}
        numbers = tuple(_extract_numbers(text))
        missing: list[str] = []
        for token in numbers:
            if _guard_key(token) not in allowed:
                missing.append(token)
        return FactualityResult(ok=not missing, numbers=numbers, missing=tuple(missing))


def fact_numbers(snapshot: AnalyticsSnapshot) -> frozenset[str]:
    """Every number the sentence may legitimately repeat.

    Both the display form ("129.00") and the canonical form ("129") are kept,
    because a narrator may write either. A date is kept as one ISO token plus
    its month-day, so quoting a known day is allowed while moving it by a day
    is not; references contribute their digit sequences.
    """
    numbers: set[str] = set()

    def add_digits(text: str) -> None:
        for token in re.findall(r"\d+(?:\.\d+)?", text):
            numbers.add(token)
            numbers.add(_canonical(token))
        # A date is one fact; its parts alone would let "2026" pass while the
        # sentence moved the answer to another day. The month-day form is kept
        # because a narrator may leave the year out.
        for date in _DATE.findall(text):
            numbers.add(date)
            canonical = _canonical_date(date)
            numbers.add(canonical)
            if len(canonical) == 10:
                numbers.add(canonical[5:])

    def add(value: Any) -> None:
        text = str(value)
        numbers.add(text)
        numbers.add(_canonical(text))
        add_digits(text)

    def add_date(value: Any) -> None:
        """A timestamp contributes a day, never its year / month / day parts.

        Registering the parts would let "20 笔" pass because a recent row
        happened on the 20th, which is a different claim about a different
        field. The whole day and its month-day key are enough; the key is a
        comparison form, not a shape the narrator is expected to quote.
        """
        text = str(value)
        for date in _DATE.findall(text):
            numbers.add(date)
            canonical = _canonical_date(date)
            numbers.add(canonical)
            if len(canonical) == 10:
                numbers.add(canonical[5:])

    add(snapshot.count)
    add(snapshot.total_cents)
    add(format_cents(snapshot.total_cents))
    add(len(snapshot.rows))
    add(snapshot.total_rows)
    add(len(snapshot.recent))
    for group in snapshot.groups:
        add(group.count)
        add(group.total_cents)
        add(format_cents(group.total_cents))
        add(group.bucket)
        add(group.label)
    for item in snapshot.recent:
        add_date(item.at)
        add_digits(item.reference)
        if item.amount_cents is not None:
            add(item.amount_cents)
            add(format_cents(item.amount_cents))
    add(snapshot.time_range)
    return frozenset(numbers)


def _extract_numbers(text: str) -> list[str]:
    found: list[str] = []
    masked = list(text)

    def blank(start: int, end: int) -> None:
        for index in range(start, end):
            masked[index] = " "

    # A rate is checked as one token too, whatever sign it uses.
    for match in _CN_PERCENT.finditer(text):
        found.append(match.group(0))
        blank(match.start(), match.end())

    # A date is checked as one token; its digits must not also pass on their
    # own, or moving the day would only have to keep the year and month.
    for match in _DATE.finditer("".join(masked)):
        found.append(match.group(0))
        blank(match.start(), match.end())

    for match in _ARABIC_NUMBER.finditer("".join(masked)):
        found.append(match.group(0))
        blank(match.start(), match.end())

    # The signed pass deliberately skips digits glued to letters so that the
    # hyphen in "ORD-DEMO-1001" stays a separator. Those digits are still a
    # claim, so a second pass reads whatever the signed pass left behind.
    for match in _PLAIN_DIGITS.finditer("".join(masked)):
        found.append(match.group(0))

    for match in _CN_NUMBER.finditer("".join(masked)):
        token = match.group(1)
        if _parse_chinese(token) is None:
            continue
        if token in {"一", "两"} and _is_determiner(
            "".join(masked), match.start(1)
        ):
            continue
        found.append(token)
    return found


def _is_determiner(text: str, start: int) -> bool:
    """True when a "一 / 两" at ``start`` names a row instead of counting.

    "最近一笔" and "第一笔" both point at a row; the numeral carries no count.
    The check is limited to "一 / 两": "前五笔" still claims five rows.
    """
    if text[max(0, start - 2) : start] == "最近":
        return True
    return start > 0 and text[start - 1] in _CN_DETERMINER_PREFIXES


def _parse_chinese(text: str) -> int | None:
    total = 0
    section = 0
    digit: int | None = None
    for char in text:
        if char in _CN_DIGITS:
            digit = _CN_DIGITS[char]
            continue
        unit = _CN_UNITS.get(char)
        if unit is None:
            return None
        if unit == 10000:
            section = (section + (digit or 0)) * unit
            total += section
            section = 0
        else:
            section += (1 if digit is None else digit) * unit
        digit = None
    if digit is not None:
        section += digit
    return total + section


def _canonical(token: str) -> str:
    text = (
        token.replace("\u2212", "-")
        .replace("\uff0d", "-")
        .replace("负", "-")
        .replace(",", "")
        .replace("\u00a0", "")
        .strip()
    )
    # A percent sign stays part of the key: the facts carry counts and amounts,
    # never rates, so "50%" must not compare equal to a count of 50.
    try:
        value = Decimal(text)
    except InvalidOperation:
        return text
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def _canonical_date(token: str) -> str:
    text = (
        token.replace("年", "-")
        .replace("月", "-")
        .replace("/", "-")
    )
    if text.endswith(("日", "号")):
        text = text[:-1]
    parts = text.split("-")
    if len(parts) == 2:
        try:
            month, day = (int(part) for part in parts)
        except ValueError:
            return token
        return f"{month:02d}-{day:02d}"
    if len(parts) != 3:
        return token
    try:
        year, month, day = (int(part) for part in parts)
    except ValueError:
        return token
    return f"{year:04d}-{month:02d}-{day:02d}"


def _guard_key(token: str) -> str:
    """Compare a token the way the guard reads it.

    A Chinese numeral is converted to its value before the lookup, so "三笔"
    and "3 笔" are the same claim. A date is normalised to ISO so "2026/9/20"
    and "2026-09-20" compare equal. The raw token is still reported when it is
    missing, because that is what the model actually wrote.
    """
    if _DATE.fullmatch(token):
        return _canonical_date(token)
    if token and all(char in _CN_DIGITS or char in _CN_UNITS for char in token):
        value = _parse_chinese(token)
        if value is not None:
            return str(value)
    return _canonical(token)


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0
