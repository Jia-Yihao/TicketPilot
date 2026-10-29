"""在线信号的出口层响应模型（口径 §4-2，tier 1）。

字段名与顺序逐字照 `.qoder/rules/online-signals-and-notification.md` §4-1：
三行信号各九字段，外加顶层 `false_positive_rate`。本模块只放形状，不放判定、
不放读数来源——判定与读数由 `app/api/v1/monitor.py` 从采集层与阈值表搬运。
"""

from __future__ import annotations

from pydantic import BaseModel


class MonitorSignalRow(BaseModel):
    """一行在线信号读数 + 出口层判定。"""

    signal_id: str
    window_minutes: int | None = None
    numerator: int
    denominator: int
    metric_value: float | None = None
    threshold: float
    breached: bool
    calibrated: bool
    action_suggestion: str


class MonitorSignalsResponse(BaseModel):
    """`GET /api/v1/monitor/signals` 的响应体。

    `false_positive_rate` 的语义是「误报数 ÷ 已复核数」，已复核数为 0 时为
    `null`（口径 §5）；它的生产者（复核写入端）属后续任务，因此本任务里
    出口恒 emit `null`——字段先留在契约上，避免后续任务改响应形状。
    """

    signals: list[MonitorSignalRow]
    false_positive_rate: float | None = None
