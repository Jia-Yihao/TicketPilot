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

__all__ = ["UNGROUNDED_MARKER", "OutputGuardResult", "guard_output", "has_assertion"]

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


def guard_output(reply: str, *, grounded: bool) -> OutputGuardResult:
    """Replace unsupported assertions when the turn has no source."""
    if not reply or grounded:
        return OutputGuardResult(reply=reply, replaced=0, kinds=())

    spans: list[tuple[int, int]] = []
    kinds: set[str] = set()
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(reply):
            spans.append((match.start(), match.end()))
            kinds.add(kind)
    if not spans:
        return OutputGuardResult(reply=reply, replaced=0, kinds=())

    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    pieces: list[str] = []
    cursor = 0
    for start, end in merged:
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
        reply=guarded, replaced=len(merged), kinds=tuple(sorted(kinds))
    )
