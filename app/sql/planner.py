"""Turning a question into a query plan.

The planner is deliberately small and deterministic. It reads a question the
way a person reads it — "how many refunds did I get last month, and how much
money was that" — and writes down *what was asked for*, never *how to query
it*. The builder turns the plan into SQL; the guard checks the SQL.

Because the plan is structured, a question the planner does not understand
produces no plan at all. That is the point: an unrecognised question must
reach a human, not a guess. The same rule applies to a status the dataset
does not have — "my failed refunds" is answered only when refunds can be
counted by status.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

__all__ = [
    "HeuristicSqlPlanner",
    "SqlPlan",
    "SqlPlanner",
    "TimeRange",
    "asks_for_aggregate",
    "names_dataset",
]


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: datetime
    end: datetime
    label: str


@dataclass(frozen=True, slots=True)
class SqlPlan:
    """What the customer asked for, in business words."""

    dataset: str
    metric: str  # "count" | "sum" | "count_and_sum"
    group_by: str | None  # None | "status" | "month" | "day"
    time_range: TimeRange | None
    question: str
    status: str | None = None


class SqlPlanner(Protocol):
    def plan(self, question: str, *, now: datetime) -> SqlPlan | None: ...


# The dataset the question is about. The first hit wins, so the more
# specific wording is checked before the general one.
_DATASET_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("refunds", ("退款", "退了", "退的", "退单", "退钱")),
    ("tickets", ("工单", "售后单", "投诉记录", "问题单")),
    ("orders", ("订单", "买过", "买了", "下单", "消费", "花了", "支付")),
)

_COUNT_CUES = ("多少笔", "几笔", "多少次", "几次", "多少单", "多少个", "几个", "数量")
_SUM_CUES = (
    "多少钱",
    "多少元",
    "总额",
    "总共",
    "一共",
    "合计",
    "花了多少",
    "退了多少",
)

_GROUP_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("status", ("按状态", "状态分布", "分布")),
    ("month", ("按月", "每月", "月度")),
    ("day", ("按天", "每天", "日度")),
)

# The nouns a record set is called by, and the measure words that may sit
# between the count word and the noun.
_RECORD_NOUNS = ("订单", "退款", "退单", "工单", "售后单", "投诉记录", "问题单")
_MEASURE_WORDS = ("笔", "个", "次", "条", "件", "款", "张")
_COUNT_WORDS = ("多少", "几")

# The status a question may ask about, per dataset. A cue that belongs to
# another dataset is a mismatch, not a detail to ignore.
_STATUS_CUES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "refunds": (
        ("SUCCEEDED", ("退款成功", "已到账", "到账", "成功")),
        ("FAILED", ("退款失败", "失败")),
        ("REQUESTED", ("已受理", "受理", "申请中", "处理中")),
        ("UNKNOWN", ("状态待确认", "状态未知")),
    ),
    "orders": (
        ("CANCELLED", ("已取消", "取消")),
        ("DELIVERED", ("已签收", "签收", "已送达")),
        ("SHIPPED", ("已发货", "发货", "运输中")),
        ("PLACED", ("已下单", "待发货", "待支付")),
    ),
    "tickets": (
        ("OPEN", ("待处理", "未处理")),
        ("IN_PROGRESS", ("处理中", "跟进中")),
        ("RESOLVED", ("已解决", "已处理")),
        ("CLOSED", ("已关闭", "关闭")),
    ),
}

_RECENT_DAYS = re.compile(r"(?:最近|近|过去)\s*(\d+)\s*天")
_RECENT_MONTHS = re.compile(r"(?:最近|近|过去)\s*(\d+)\s*个月")


def names_dataset(text: str) -> str | None:
    """The record set the question is about, if it names one."""
    lowered = text.lower()
    for name, cues in _DATASET_CUES:
        if any(cue in lowered for cue in cues):
            return name
    return None


def asks_for_aggregate(text: str) -> bool:
    """True when the question asks for a count, a sum or a distribution."""
    lowered = text.lower()
    if any(cue in lowered for cue in _COUNT_CUES):
        return True
    if any(cue in lowered for cue in _SUM_CUES):
        return True
    if _counts_a_record_set(lowered):
        return True
    return any(cue in lowered for _name, cues in _GROUP_CUES for cue in cues)


def _counts_a_record_set(text: str) -> bool:
    """True when a count word sits next to the name of a record set.

    "多少订单" and "订单有多少" both count records; the count word may
    also be followed by a measure word ("多少笔退款").
    """
    for word in _COUNT_WORDS:
        index = text.find(word)
        while index != -1:
            before = text[:index]
            tail = text[index + len(word) :]
            if _names_record_set_at_end(before) or _names_record_set_at_start(tail):
                return True
            index = text.find(word, index + 1)
    return False


def _names_record_set_at_end(fragment: str) -> bool:
    if not fragment:
        return False
    if any(fragment.endswith(noun) for noun in _RECORD_NOUNS):
        return True
    for measure in _MEASURE_WORDS:
        if fragment.endswith(measure):
            head = fragment[: -len(measure)]
            if any(noun in head for noun in _RECORD_NOUNS):
                return True
    return False


def _names_record_set_at_start(fragment: str) -> bool:
    if not fragment:
        return False
    if any(fragment.startswith(noun) for noun in _RECORD_NOUNS):
        return True
    if fragment[0] in _MEASURE_WORDS:
        rest = fragment[1:]
        return any(rest.startswith(noun) for noun in _RECORD_NOUNS)
    return False


def _detect_status(text: str, dataset: str) -> tuple[str | None, bool]:
    """The status the question asks about, and whether it fits the dataset.

    The second value is True when a status word was found that belongs to
    another record set: the question then cannot be answered honestly.
    """
    found: list[tuple[str, str]] = []
    for name, entries in _STATUS_CUES.items():
        for status, cues in entries:
            if any(cue in text for cue in cues):
                found.append((name, status))
    if not found:
        return None, False
    own = [status for name, status in found if name == dataset]
    if own:
        return own[0], False
    return None, True


class HeuristicSqlPlanner:
    """Reads a question and writes down the query it describes."""

    def plan(self, question: str, *, now: datetime) -> SqlPlan | None:
        text = question.strip().lower()
        if not text:
            return None
        dataset = self._dataset(text)
        if dataset is None:
            return None
        status, mismatched = _detect_status(text, dataset)
        if mismatched:
            return None
        metric = self._metric(text)
        group_by = self._group_by(text)
        time_range = self._time_range(text, now)
        return SqlPlan(
            dataset=dataset,
            metric=metric,
            group_by=group_by,
            time_range=time_range,
            question=question.strip(),
            status=status,
        )

    # ------------------------------------------------------------ internals
    def _dataset(self, text: str) -> str | None:
        return names_dataset(text)

    def _metric(self, text: str) -> str:
        has_count = any(cue in text for cue in _COUNT_CUES)
        has_sum = any(cue in text for cue in _SUM_CUES)
        if has_count and has_sum:
            return "count_and_sum"
        if has_sum:
            return "sum"
        return "count"

    def _group_by(self, text: str) -> str | None:
        for name, cues in _GROUP_CUES:
            if any(cue in text for cue in cues):
                return name
        return None

    def _time_range(self, text: str, now: datetime) -> TimeRange | None:
        if "今天" in text:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return TimeRange(start, start + timedelta(days=1), "今天")
        if "昨天" in text:
            start = (now - timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            return TimeRange(start, start + timedelta(days=1), "昨天")
        if "上周" in text:
            this_monday = _monday(now)
            start = this_monday - timedelta(days=7)
            return TimeRange(start, this_monday, "上周")
        if "本周" in text or "这周" in text:
            start = _monday(now)
            return TimeRange(start, start + timedelta(days=7), "本周")
        if "上个月" in text or "上月" in text:
            start = _month_start(_add_month(now, -1))
            return TimeRange(start, _month_start(now), "上个月")
        if "本月" in text or "这个月" in text or "当月" in text:
            start = _month_start(now)
            return TimeRange(start, _month_start(_add_month(now)), "本月")
        if "去年" in text:
            start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            start = start.replace(year=start.year - 1)
            return TimeRange(start, start.replace(year=start.year + 1), "去年")
        if "今年" in text:
            start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            return TimeRange(start, start.replace(year=start.year + 1), "今年")
        days = _RECENT_DAYS.search(text)
        if days is not None:
            count = max(1, min(365, int(days.group(1))))
            return TimeRange(now - timedelta(days=count), now, f"最近 {count} 天")
        months = _RECENT_MONTHS.search(text)
        if months is not None:
            count = max(1, min(36, int(months.group(1))))
            start = _month_start(_add_month(now, -count))
            return TimeRange(start, now, f"最近 {count} 个月")
        if "最近" in text or "近期" in text:
            return TimeRange(now - timedelta(days=30), now, "最近 30 天")
        return None


def _monday(moment: datetime) -> datetime:
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return start - timedelta(days=start.weekday())


def _month_start(moment: datetime) -> datetime:
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_month(moment: datetime, offset: int = 1) -> datetime:
    """Shift by whole months; the day is clamped to the target month length."""
    total = moment.year * 12 + (moment.month - 1) + offset
    year, month = divmod(total, 12)
    month += 1
    day = min(moment.day, _days_in_month(year, month))
    return moment.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (datetime(year, month + 1, 1) - timedelta(days=1)).day
