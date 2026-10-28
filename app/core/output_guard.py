"""Output-side grounding for amounts, timelines and commitments.

A customer-facing sentence may only state a number when the turn has
something to point at: a task result, a context card, or an action that read
the authoritative record. Everything else is a claim the agent cannot back.

The guard replaces just the unsupported assertion with a visible marker. It
never drops the whole reply: the deterministic part of the answer is still
useful, and silently deleting a sentence would hide the problem from the
customer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "UNGROUNDED_MARKER",
    "OutputGuardResult",
    "assertion_spans",
    "guard_output",
    "has_assertion",
]

UNGROUNDED_MARKER = "需确认"

_CAVEAT = "以上「需确认」的信息缺少可核对的来源，请以订单详情或人工核实为准。"

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "money",
        re.compile(
            r"(?:[¥￥]\s*)?\d+(?:[.,]\d+)?\s*"
            r"(?:元|块钱|CNY|RMB|USD|美元|欧元|港币)",
            re.IGNORECASE,
        ),
    ),
    (
        "timeline",
        re.compile(
            r"(?:\d+|[一二两三四五六七八九十]+)\s*个?\s*"
            r"(?:工作日|自然日|小时|分钟|天|周|个月|月)\s*(?:内|之内|以内)?"
        ),
    ),
    ("percentage", re.compile(r"\d+(?:\.\d+)?\s*%")),
    (
        "commitment",
        re.compile(
            r"(?:保证|承诺|一定|必然|肯定|百分之百)\s*(?:会|能|可以|在)?"
            r"[^，。；\n]{0,16}(?:退款|退货|赔付|到账|送达|解决)"
        ),
    ),
    (
        "immediate",
        re.compile(
            r"(?:今天|明天|马上|立刻|立即)\s*(?:就|会|可以)?"
            r"[^，。；\n]{0,12}(?:退款|到账|送达|处理完)"
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class OutputGuardResult:
    reply: str
    replaced: int
    kinds: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return self.replaced > 0


def has_assertion(reply: str) -> bool:
    return any(pattern.search(reply) for _, pattern in _PATTERNS)


def _assertion_matches(reply: str) -> list[tuple[str, int, int]]:
    """Every pattern hit as ``(kind, start, end)``, in pattern order.

    This is the one place that decides what counts as an unsupported
    assertion: the guard replaces these spans, and the answer-layer number
    traceability check reads the same spans. Two copies of the regular
    expressions would eventually disagree.
    """
    found: list[tuple[str, int, int]] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(reply):
            found.append((kind, match.start(), match.end()))
    return found


def _merged(
    matches: list[tuple[str, int, int]],
) -> list[tuple[str, int, int]]:
    """Sort by position and merge overlapping spots into one span.

    The span keeps the kind of the earliest match; merging never grows the
    text it covers beyond the union of its parts.
    """
    ordered = sorted(matches, key=lambda item: (item[1], item[2]))
    merged: list[tuple[str, int, int]] = []
    for kind, start, end in ordered:
        if merged and start <= merged[-1][2]:
            first_kind, first_start, first_end = merged[-1]
            merged[-1] = (first_kind, first_start, max(first_end, end))
        else:
            merged.append((kind, start, end))
    return merged


def assertion_spans(reply: str) -> tuple[tuple[str, int, int], ...]:
    """(kind, start, end) for every unsupported-assertion span, in text order.

    ``kind`` is one of ``money`` / ``timeline`` / ``percentage`` /
    ``commitment`` / ``immediate``. Spans are sorted by ``start`` and never
    overlap, so ``reply[start:end]`` slices out the exact matched text.
    """
    return tuple(_merged(_assertion_matches(reply)))


def guard_output(reply: str, *, grounded: bool) -> OutputGuardResult:
    """Replace unsupported assertions when the turn has no source."""
    if not reply or grounded:
        return OutputGuardResult(reply=reply, replaced=0, kinds=())

    spans = assertion_spans(reply)
    if not spans:
        return OutputGuardResult(reply=reply, replaced=0, kinds=())
    # `kinds` reports every pattern that hit, including one whose span was
    # merged into a neighbouring span; that is why it is read from the raw
    # matches rather than from the merged spans.
    kinds = {kind for kind, _, _ in _assertion_matches(reply)}

    pieces: list[str] = []
    cursor = 0
    for _, start, end in spans:
        pieces.append(reply[cursor:start])
        pieces.append(UNGROUNDED_MARKER)
        cursor = end
    pieces.append(reply[cursor:])
    guarded = "".join(pieces)
    guarded = re.sub(
        rf"(?:{UNGROUNDED_MARKER}[\s、，,；;]*)+", UNGROUNDED_MARKER, guarded
    )
    guarded = " ".join(guarded.split())
    if not guarded.endswith(("。", "！", "？", ".", "!", "?")):
        guarded = f"{guarded}。"
    guarded = f"{guarded} {_CAVEAT}"
    return OutputGuardResult(
        reply=guarded, replaced=len(spans), kinds=tuple(sorted(kinds))
    )
