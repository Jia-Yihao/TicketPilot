"""Answer-layer readings: citation hit, number traceability, fabrication, guard.

The support runner checks that the conversation understood the message. This
runner checks the **answer** itself, with four deterministic criteria. None of
them needs a judging model, so every reading can be recomputed offline from
the same corpus.

The four criteria
=================

1. ``citation_hit`` — did the answer point at a section we consider correct?
   The answer's policy evidence is read from the ``retrieve/policy.search``
   span (``citations[]``: ``document_id`` / ``version`` / ``section``) plus the
   ``matched_section`` of every ``kind=policy`` context card, because those are
   two faces of the same fact. The case passes when at least one of those
   section titles is in the case's expected ``sections``. A turn with no policy
   card and ``hits == 0`` is **not** a hit; it is counted separately as
   ``no_evidence`` — "retrieved nothing" and "retrieved the wrong thing" are
   different failures and must not share a denominator silently.

2. ``untraceable_number`` — can every number in the answer be found again?
   ``app.core.output_guard.assertion_spans()`` — the same definition the output
   guard replaces on — yields the ``money`` / ``timeline`` / ``percentage``
   spans. Each span is normalised to ``(value, unit)``: Chinese numerals 1–99
   become Arabic digits, ``天``/``日`` both mean 天, ``个工作日``/``工作日`` mean
   工作日, ``周``/``星期`` mean 周, ``个月``/``月`` mean 个月, and money units
   (``元`` / ``块钱`` / ``CNY`` / ``RMB``) mean 元. The white list is built
   **only** from

   * the full text of the policy sections the answer actually cited, fetched
     through ``GET /api/v1/support/cards/policy/{document_id}/sections/{id}``;
   * structured numbers in the response body: ``context_cards[].fields`` and
     ``task.result``, where a ``*_cents`` field contributes ``(value/100, 元)``.

   The reply itself is never a source — that would make the check circular.
   ``commitment`` / ``immediate`` are wording assertions, not numbers; they are
   covered by the guard criterion (4) and deliberately left out of this one.

   A clarification turn (``clarification != None``) is skipped: the example
   amounts in a clarification prompt are not an answer, and the guard skips the
   same turns.

3. ``fabrication`` — does the answer contain a phrase from the case's
   ``forbidden`` list? The list holds claims the current policy does not
   support: wording unique to the expired ``refund-v2.md`` (15 天, 必须已签收)
   and unprovable promises (保证退款, 百分之百, 需审批豁免类说法). Matching is a
   plain substring test on the reply.

4. ``guard_replacement`` — how much of the answer did the output guard rewrite?
   Two readings, printed separately and never added together:

   * ``guard.corpus_turns`` / ``guard.corpus_replaced`` — turns in the corpus
     whose ``guard/output.grounded`` span reports ``replacements > 0``. In rules
     mode this is expected to be **0**: every branch that states a number also
     carries a card or a task result, so the guard has a source and stays
     quiet. That zero is a structural fact, not evidence the guard is broken.
   * ``guard.probe_replaced`` / ``guard.probe_total`` — a probe that calls
     ``guard_output()`` directly on five unsourced sentences, one per assertion
     kind, and on five grounded equivalents of the same sentences. Only the
     five unsourced ones count towards the denominator; the grounded five are
     reported as ``probe_grounded_untouched``. That second reading does not
     re-judge the sentence: ``guard_output(..., grounded=True)`` takes the
     early return reserved for turns that already have a source, so it pins the
     contract "a turn with a source is left alone", not a static property of
     the text.

   The non-zero reading comes from the probe. It must never be quoted as a
   corpus reading.

Usage::

    python evals/answer_metrics.py                # print readings, exit 0
    python evals/answer_metrics.py --enforce      # gate: three rates + probe + any non-200
    python evals/answer_metrics.py --partial --dataset /tmp/subset.json

``--enforce`` compares the **rates** the summary prints, not individual cases:
``citation_hit_rate`` below its threshold, ``fabrication_rate`` or
``untraceable_number_rate`` above theirs, either probe reading short, or any
case whose request did not return 200 — a failed request is not a quality
reading, so it is never averaged into a rate. The per-case ``VIOLATION`` lines
are diagnostics and do not decide the exit code; one citation miss out of sixty
is 0.9833, which the declared threshold allows.

``--partial`` skips the corpus shape checks (case count, unique ids, section
existence, no copied policy sentences) so a single case can be iterated on; it
never replaces the full gate.

Not wired into ``evals/metrics_baseline.py`` yet: that baseline is defined as
five offline sources, and adding a sixth would change a documented reading.
Wiring it in later means extending ``SOURCES`` and recording the baseline then.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import re
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.output_guard import assertion_spans, guard_output
from app.main import create_app
from app.seed import seed_all

try:
    from evals.fixtures import apply_fixtures
    from evals.run import _headers
except ModuleNotFoundError:  # `python evals/answer_metrics.py` puts evals/ on sys.path
    from fixtures import apply_fixtures
    from run import _headers

ROOT = Path(__file__).resolve().parent.parent
POLICY_DIR = ROOT / "app" / "resources" / "policies"

MIN_CASES = 50
_SECTION_OVERLAP_CHARS = 12
DEFAULT_THRESHOLDS = {
    "citation_hit_rate": 0.9,
    "fabrication_rate": 0.0,
    "untraceable_number_rate": 0.0,
}

# Only the four current documents. refund-v2.md is expired on purpose and must
# never be a case's expected policy: the retrieval layer has to reject it.
POLICY_FILES = {
    "POLICY-REFUND": "refund-v3.md",
    "POLICY-RETURN": "return-v1.md",
    "POLICY-DELAY": "delay-v1.md",
    "POLICY-TICKET": "ticket-v1.md",
}

_NUMBER_KINDS = frozenset({"money", "timeline", "percentage"})

_ACTOR = {"tenant": "demo", "user": "alice"}


# --------------------------------------------------------------- corpus shape
def _document_sections(filename: str) -> dict[str, str]:
    """`## title` -> body, read from the markdown the app itself loads."""
    text = (POLICY_DIR / filename).read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    title: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if title is not None:
                sections[title] = "\n".join(body).strip()
            title = line[3:].strip()
            body = []
        elif title is not None:
            body.append(line)
    if title is not None:
        sections[title] = "\n".join(body).strip()
    return sections


def _policy_corpus() -> dict[str, dict[str, str]]:
    return {name: _document_sections(name) for name in POLICY_FILES.values()}


def _all_policy_text() -> list[str]:
    return [
        path.read_text(encoding="utf-8")
        for path in sorted(POLICY_DIR.glob("*.md"))
    ]


def _copies_policy(question: str) -> str | None:
    """Return the copied fragment when the question quotes policy prose."""
    for text in _all_policy_text():
        for start in range(0, max(len(question) - _SECTION_OVERLAP_CHARS + 1, 0)):
            window = question[start : start + _SECTION_OVERLAP_CHARS]
            if window and window in text:
                return window
    return None


def validate_dataset(dataset: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    cases = dataset.get("cases") or []
    corpus = _policy_corpus()
    seen: set[str] = set()
    for index, case in enumerate(cases):
        label = str(case.get("id") or f"case[{index}]")
        if not case.get("id"):
            failures.append(f"{label}: missing id")
        elif label in seen:
            failures.append(f"{label}: duplicate case id")
        seen.add(label)

        question = str(case.get("question") or "").strip()
        if not question:
            failures.append(f"{label}: empty question")
        else:
            copied = _copies_policy(question)
            if copied is not None:
                failures.append(f"{label}: question copies policy text {copied!r}")

        policy_id = case.get("policy_id")
        if policy_id not in POLICY_FILES:
            failures.append(f"{label}: unknown policy_id {policy_id!r}")
        else:
            known = corpus[POLICY_FILES[policy_id]]
            sections = case.get("sections") or []
            if not sections:
                failures.append(f"{label}: empty sections")
            for title in sections:
                if title not in known:
                    failures.append(
                        f"{label}: section {title!r} does not exist in "
                        f"{POLICY_FILES[policy_id]}"
                    )

        forbidden = case.get("forbidden") or []
        if not forbidden:
            failures.append(f"{label}: empty forbidden list")

    if len(cases) < MIN_CASES:
        failures.append(
            f"dataset: expected at least {MIN_CASES} cases, found {len(cases)}"
        )
    return failures


# ---------------------------------------------------------- number normalising
_MONEY = re.compile(
    r"(?:[¥￥]\s*)?(\d+(?:[.,]\d+)?)\s*(元|块钱|CNY|RMB|USD|美元|欧元|港币)",
    re.IGNORECASE,
)
_TIMELINE = re.compile(
    r"(\d+|[一二两三四五六七八九十]+)\s*个?\s*"
    r"(工作日|自然日|小时|分钟|天|日|周|个月|月)"
)
_PERCENTAGE = re.compile(r"(\d+(?:\.\d+)?)\s*%")

_DIGITS = {
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
_UNITS = {
    "天": "天",
    "日": "天",
    "工作日": "工作日",
    "自然日": "自然日",
    "小时": "小时",
    "分钟": "分钟",
    "分": "分钟",
    "周": "周",
    "星期": "周",
    "个月": "个月",
    "月": "个月",
}
_MONEY_UNITS = {
    "元": "元",
    "块钱": "元",
    "CNY": "元",
    "RMB": "元",
    "USD": "美元",
    "美元": "美元",
    "欧元": "欧元",
    "港币": "港币",
}


def _to_int(raw: str) -> int | None:
    """Arabic digits, or the common Chinese numerals 1–99 (no fanciful parser)."""
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if len(raw) == 1:
        return _DIGITS.get(raw)
    if len(raw) == 2:
        if raw[0] == "十":
            tail = _DIGITS.get(raw[1])
            return None if tail is None else 10 + tail
        if raw[1] == "十":
            head = _DIGITS.get(raw[0])
            return None if head is None else head * 10
    return None


def _decide(raw: str) -> Decimal | None:
    """One captured number, with the comma disambiguated by grouping."""
    text = raw
    if "," in text:
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", text):
            text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def normalize_span(kind: str, text: str) -> tuple[Decimal, str] | None:
    """``(value, unit)`` for one assertion span, or None when it is not numeric."""
    if kind == "money":
        match = _MONEY.search(text)
        if match is None:
            return None
        value = _decide(match.group(1))
        unit = _MONEY_UNITS.get(match.group(2).upper(), _MONEY_UNITS.get(match.group(2)))
        if value is None or unit is None:
            return None
        return value, unit
    if kind == "timeline":
        match = _TIMELINE.search(text)
        if match is None:
            return None
        number = _to_int(match.group(1))
        unit = _UNITS.get(match.group(2))
        if number is None or unit is None:
            return None
        return Decimal(number), unit
    if kind == "percentage":
        match = _PERCENTAGE.search(text)
        if match is None:
            return None
        value = _decide(match.group(1))
        if value is None:
            return None
        return value, "%"
    return None


def number_spans(text: str) -> list[tuple[int, int, tuple[Decimal, str]]]:
    """Every numeric assertion in ``text`` as ``(start, end, (value, unit))``."""
    found: list[tuple[int, int, tuple[Decimal, str]]] = []
    for kind, start, end in assertion_spans(text):
        if kind not in _NUMBER_KINDS:
            continue
        normalised = normalize_span(kind, text[start:end])
        if normalised is not None:
            found.append((start, end, normalised))
    return found


def whitelist_numbers(text: str) -> set[tuple[Decimal, str]]:
    return {(value, unit) for _, _, (value, unit) in number_spans(text)}


def _structured_numbers(payload: Any, found: set[tuple[Decimal, str]]) -> None:
    """Money in a card field or a task result, mapped from cents to 元.

    Only ``*_cents`` keys are taken. A bare count is not a source for a
    ``(value, unit)`` pair — letting one through would let any integer in the
    payload vouch for ``15 天``.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if (
                str(key).endswith("_cents")
                and isinstance(value, int)
                and not isinstance(value, bool)
            ):
                found.add((Decimal(value) / Decimal(100), "元"))
            else:
                _structured_numbers(value, found)
    elif isinstance(payload, list):
        for item in payload:
            _structured_numbers(item, found)


# ------------------------------------------------------------- trace reading
def _span(trace_body: dict[str, Any] | None, kind: str, name: str) -> dict[str, Any] | None:
    for span in (trace_body or {}).get("spans") or []:
        if span.get("kind") == kind and span.get("name") == name:
            return span
    return None


def _fetch(client: TestClient, url: str, headers: dict[str, str]) -> dict[str, Any] | None:
    response = client.get(url, headers=headers)
    if response.status_code != 200:
        return None
    return response.json()


def _cited_sections(
    body: dict[str, Any], trace_body: dict[str, Any] | None
) -> tuple[set[str], set[tuple[str, str]], bool]:
    """Return (section titles, (document_id, title) pairs, has evidence)."""
    search = _span(trace_body, "retrieve", "policy.search") or {}
    attributes = search.get("attributes") or {}
    titles: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for citation in attributes.get("citations") or []:
        section = citation.get("section")
        document_id = citation.get("document_id")
        if section and document_id:
            titles.add(section)
            pairs.add((document_id, section))
    cards = [card for card in body.get("context_cards") or [] if card.get("kind") == "policy"]
    for card in cards:
        section = (card.get("fields") or {}).get("matched_section")
        if section:
            titles.add(section)
            pairs.add((card.get("card_id"), section))
    hits = attributes.get("hits")
    has_evidence = bool(cards) or (isinstance(hits, int) and hits > 0)
    return titles, pairs, has_evidence


def _section_whitelist(
    client: TestClient,
    headers: dict[str, str],
    body: dict[str, Any],
    pairs: set[tuple[str, str]],
) -> set[tuple[Decimal, str]]:
    found: set[tuple[Decimal, str]] = set()
    section_ids: dict[tuple[str, str], str] = {}
    for card in body.get("context_cards") or []:
        if card.get("kind") != "policy":
            continue
        for section in card.get("sections") or []:
            section_ids[(card.get("card_id"), section.get("title"))] = section.get("id")
    for document_id, title in pairs:
        section_id = section_ids.get((document_id, title))
        if not section_id:
            continue
        detail = _fetch(
            client,
            f"/api/v1/support/cards/policy/{document_id}/sections/{section_id}",
            headers,
        )
        content = ((detail or {}).get("section") or {}).get("content")
        if content:
            found |= whitelist_numbers(content)
    _structured_numbers(body.get("context_cards") or [], found)
    _structured_numbers((body.get("task") or {}).get("result") or {}, found)
    return found


# ------------------------------------------------------------------- the probe
_GUARD_PROBES: tuple[tuple[str, str], ...] = (
    ("money", "这笔退款一共 129 元，已经处理好了。"),
    ("timeline", "签收后 15 天内提交申请都可以。"),
    ("percentage", "退款成功率达到 99% 以上。"),
    ("commitment", "我们保证一定退款，请放心。"),
    ("immediate", "明天就会到账。"),
)


def probe_guard() -> dict[str, int]:
    replaced = 0
    grounded_untouched = 0
    for _, sentence in _GUARD_PROBES:
        if guard_output(sentence, grounded=False).replaced > 0:
            replaced += 1
        if guard_output(sentence, grounded=True).replaced == 0:
            grounded_untouched += 1
    return {
        "probe_replaced": replaced,
        "probe_total": len(_GUARD_PROBES),
        "probe_grounded_untouched": grounded_untouched,
    }


# ------------------------------------------------------------------ the run
def _evaluate(
    client: TestClient,
    headers: dict[str, str],
    case: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/support/messages",
        headers=headers,
        json={"message": case["question"]},
    )
    body = response.json()
    trace_id = body.get("trace_id")
    trace_body = _fetch(client, f"/api/v1/traces/{trace_id}", headers) if trace_id else None
    reply = str(body.get("reply") or "")
    expected = set(case.get("sections") or [])

    titles, pairs, has_evidence = _cited_sections(body, trace_body)
    hit = bool(titles & expected)
    no_evidence = not has_evidence

    untraceable: list[str] = []
    skipped_for_clarification = body.get("clarification") is not None
    if not skipped_for_clarification:
        allowed = _section_whitelist(client, headers, body, pairs)
        for start, end, (value, unit) in number_spans(reply):
            if (value, unit) in allowed:
                continue
            untraceable.append(f"{reply[start:end]!r} -> ({value}, {unit})")

    fabricated = [item for item in case.get("forbidden") or [] if item in reply]

    guard_span = _span(trace_body, "guard", "output.grounded")
    replacements = int((guard_span or {}).get("attributes", {}).get("replacements") or 0)

    return {
        "id": case["id"],
        "reply": reply,
        "sections": sorted(titles),
        "hit": hit,
        "no_evidence": no_evidence,
        "untraceable": untraceable,
        "fabricated": fabricated,
        "clarification": skipped_for_clarification,
        "guard_replacements": replacements,
        "status": response.status_code,
    }


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _report(
    readings: list[dict[str, Any]],
    thresholds: dict[str, float],
    probe: dict[str, int],
) -> dict[str, Any]:
    total = len(readings)
    hits = sum(1 for item in readings if item["hit"])
    no_evidence = sum(1 for item in readings if item["no_evidence"])
    fabrications = sum(1 for item in readings if item["fabricated"])
    untraceable = sum(1 for item in readings if item["untraceable"])
    # A clarification turn never reaches the guard, so it produces no
    # `guard/output.grounded` span and must not sit in that denominator.
    # Readings built by hand may omit the key; a missing key is not a
    # clarification turn.
    corpus_turns = sum(1 for item in readings if not item.get("clarification"))
    corpus_replaced = sum(1 for item in readings if item["guard_replacements"] > 0)

    print(f"answer cases: {total}")
    print(
        f"citation_hit: {hits}/{total} = {_rate(hits, total):.4f} "
        f"(threshold {thresholds['citation_hit_rate']})  no_evidence: {no_evidence}"
    )
    print(
        f"fabrication: {fabrications}/{total} = {_rate(fabrications, total):.4f} "
        f"(threshold {thresholds['fabrication_rate']})"
    )
    print(
        f"untraceable_number: {untraceable}/{total} = {_rate(untraceable, total):.4f} "
        f"(threshold {thresholds['untraceable_number_rate']})"
    )
    print(f"guard.corpus_turns: {corpus_turns}  guard.corpus_replaced: {corpus_replaced}")
    print(
        "  corpus-level replacements are structurally 0 in rules mode: every branch that "
        "states a number carries a card or a task result, so the guard has a source."
    )
    print(
        f"guard.probe_replaced: {probe['probe_replaced']}/{probe['probe_total']}  "
        f"probe_grounded_untouched: {probe['probe_grounded_untouched']}/"
        f"{probe['probe_total']}"
    )
    print(
        "  the non-zero replacement count comes from the probe, not from the corpus; "
        "the two are different denominators."
    )
    return {
        "cases": total,
        "citation_hit": hits,
        "no_evidence": no_evidence,
        "fabrication": fabrications,
        "untraceable": untraceable,
        "guard_turns": corpus_turns,
        "guard_replaced": corpus_replaced,
        **probe,
    }


def _failures(readings: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    for item in readings:
        if not item["hit"]:
            detail = "no evidence retrieved" if item["no_evidence"] else f"cited {item['sections']}"
            items.append(f"{item['id']}: citation_miss ({detail})")
        if item["fabricated"]:
            items.append(f"{item['id']}: fabricated {item['fabricated']}")
        if item["untraceable"]:
            items.append(f"{item['id']}: untraceable {item['untraceable']}")
        if item["status"] != 200:
            items.append(f"{item['id']}: http {item['status']}")
    return items


def _gate_failures(
    counts: dict[str, Any],
    thresholds: dict[str, float],
    probe: dict[str, int],
    http_errors: int,
) -> list[str]:
    """The gate items. An empty list means the run passed.

    The gate looks at the **rates** in ``counts`` (the dict ``_report``
    returns), never at a single case: ``citation_hit=53/60`` is below 0.9 and
    fails, ``54/60`` is exactly 0.9 and passes, one miss in sixty (0.9833) is
    inside the declared tolerance. Per-case misses are printed as ``VIOLATION``
    diagnostics and deliberately do not decide the exit code.

    A request that did not return 200 is not a quality reading at all — it is
    missing data — so any non-200 case fails the gate outright.
    """
    cases = int(counts["cases"])
    failures: list[str] = []

    citation = _rate(int(counts["citation_hit"]), cases)
    if cases == 0:
        # Dividing by zero must not read as "no misses": an empty run has no
        # evidence that the answer layer works.
        failures.append(
            "citation_hit: no cases scored, an empty run cannot meet "
            f"citation_hit_rate {thresholds['citation_hit_rate']}"
        )
    elif citation < thresholds["citation_hit_rate"]:
        failures.append(
            f"citation_hit {counts['citation_hit']}/{cases} = {citation:.4f} "
            f"< citation_hit_rate {thresholds['citation_hit_rate']}"
        )

    fabrication = _rate(int(counts["fabrication"]), cases)
    if fabrication > thresholds["fabrication_rate"]:
        failures.append(
            f"fabrication {counts['fabrication']}/{cases} = {fabrication:.4f} "
            f"> fabrication_rate {thresholds['fabrication_rate']}"
        )

    untraceable = _rate(int(counts["untraceable"]), cases)
    if untraceable > thresholds["untraceable_number_rate"]:
        failures.append(
            f"untraceable_number {counts['untraceable']}/{cases} = "
            f"{untraceable:.4f} > untraceable_number_rate "
            f"{thresholds['untraceable_number_rate']}"
        )

    if http_errors > 0:
        failures.append(f"http_errors: {http_errors} case(s) did not return 200")

    if not (probe["probe_replaced"] == probe["probe_total"] == 5):
        failures.append("guard probe did not replace all five unsourced sentences")
    if probe["probe_grounded_untouched"] != 5:
        failures.append("guard probe touched a grounded sentence")

    return failures


def _run_dataset(
    dataset: dict[str, Any],
    database_url: str,
    partial: bool = False,
    enforce: bool = False,
) -> int:
    if not partial:
        dataset_failures = validate_dataset(dataset)
        if dataset_failures:
            for item in dataset_failures:
                print(f"DATASET {item}")
            return 2
    elif not dataset.get("cases"):
        print("DATASET partial set has no cases")
        return 2

    settings = Settings(
        environment="test",
        database_url=database_url,
        auto_create_tables=True,
    )
    app = create_app(settings)
    seed_all(app.state.session_factory)
    apply_fixtures(app.state.session_factory)
    client = TestClient(app)
    headers = _headers(_ACTOR)

    readings: list[dict[str, Any]] = []
    for case in dataset["cases"]:
        try:
            reading = _evaluate(client, headers, case)
        except Exception as exc:  # one broken case must not hide the rest
            reading = {
                "id": case.get("id", "?"),
                "reply": "",
                "sections": [],
                "hit": False,
                "no_evidence": True,
                "untraceable": [],
                "fabricated": [],
                "clarification": False,
                "guard_replacements": 0,
                "status": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }
        readings.append(reading)
        marker = (
            "PASS"
            if reading["hit"]
            and not reading["untraceable"]
            and not reading["fabricated"]
            and reading["status"] == 200
            else "FAIL"
        )
        print(f"{marker} {reading['id']} sections={reading['sections']}")
        if reading.get("error"):
            print(f"     - {reading['error']}")

    app.state.engine.dispose()
    thresholds = {**DEFAULT_THRESHOLDS, **(dataset.get("thresholds") or {})}
    probe = probe_guard()
    counts = _report(readings, thresholds, probe)

    # Diagnostics only: one miss is not a gate failure unless it moves the rate
    # out of tolerance. The exit code comes from `_gate_failures` below.
    failures = _failures(readings)
    for item in failures:
        print(f"VIOLATION {item}")

    if not enforce:
        return 0

    http_errors = sum(1 for item in readings if item["status"] != 200)
    gate_failures = _gate_failures(counts, thresholds, probe, http_errors)
    for item in gate_failures:
        print(f"GATE {item}")
    if gate_failures:
        print("ANSWER FAILED")
        return 1
    print("ANSWER OK")
    return 0


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(
        description="Run the answer-layer criteria over the golden answer set."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).with_name("answer_set.json"),
    )
    parser.add_argument(
        "--database-url",
        help="Run against this database instead of a temporary SQLite file.",
    )
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Skip the corpus shape checks (case count, ids, sections, copied policy).",
    )
    parser.add_argument(
        "--enforce",
        action="store_true",
        help=(
            "Exit 1 when a rate is outside its threshold, a guard probe reading "
            "is short, or any case did not return 200."
        ),
    )
    args = parser.parse_args()

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    if args.database_url:
        return _run_dataset(dataset, args.database_url, partial=args.partial, enforce=args.enforce)
    with tempfile.TemporaryDirectory() as directory:
        url = f"sqlite+pysqlite:///{Path(directory) / 'answer.db'}"
        return _run_dataset(dataset, url, partial=args.partial, enforce=args.enforce)


if __name__ == "__main__":
    sys.exit(main())
