"""在线信号的出口层请求与响应模型（口径 §4-2/§4-7，tier 1）。

字段名与顺序逐字照 `.qoder/rules/online-signals-and-notification.md` §4-1：
三行信号各九字段，外加顶层 `false_positive_rate`。本模块只放形状，不放判定、
不放读数来源——判定与读数由 `app/api/v1/monitor.py` 从采集层、阈值表与服务层
`app/services/signal_audit` 搬运。§4-7 的复核请求形状（`MonitorReviewRequest`）
也放在这里：只定字段，写入的生产者与校验都在服务层。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# 复核请求体里 `window_minutes` 的上界，逐字照口径 §2 通用规则 `le=60*24*90`。
# 本仓各出口模块（`traces`/`security`/`monitor` 出口）都各自本地声明同名上界，
# schemas 层不反向依赖 api 层，故此处独立声明一份；越界由 pydantic 校验为 422。
MAX_WINDOW_MINUTES = 60 * 24 * 90


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
    `null`（口径 §5），与「复核过且全对」的 `0.0` 必须可分。计算由服务层
    `app/services/signal_audit.false_positive_rate` 产出、出口层接线；reviewed
    行的写入请求由下方 `MonitorReviewRequest` 承载、经出口层
    `POST /api/v1/monitor/reviews` 落库，本模块只定形状。
    """

    signals: list[MonitorSignalRow]
    false_positive_rate: float | None = None


class MonitorReviewRequest(BaseModel):
    """`POST /api/v1/monitor/reviews` 的请求体（口径 §4-7）。

    仅三字段，逐字照 §4-7：`signal_id`（封闭词表由服务层用阈值表校验，本形状
    不另立字面清单）、`conclusion`（值域 `FALSE_POSITIVE` / `CONFIRMED` 由服务层
    `REVIEW_OUTCOMES` 单一定义处校验）、可选 `window_minutes`（不给即全量窗口，
    边界 `ge=1, le=60*24*90` 在此声明、越界由 pydantic 判 422）。不加任何其它字段。
    """

    signal_id: str
    conclusion: str
    window_minutes: int | None = Field(default=None, ge=1, le=MAX_WINDOW_MINUTES)


class MonitorReviewResponse(BaseModel):
    """`POST /api/v1/monitor/reviews` 的响应体——受理回执（裁定，见交付说明）。

    只回显本次被受理的复核三要素，不返回信号表、不重算 `false_positive_rate`：
    误报率仍由只读端点 `GET /api/v1/monitor/signals` 单独可读（口径 §4-1/§5），
    复核端点不制造第二份读数路径。校验不通过的请求走错误信封，不会产成本模型。
    """

    signal_id: str
    conclusion: str
    window_minutes: int | None = None
