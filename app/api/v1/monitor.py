"""运维信号出口：三类在线信号的读取 HTTP 端点（口径 §4-1/§4-4/§5）。

这一层**只做搬运、判定与调用**：把采集层的读数逐字搬出来、把阈值判定算成
`breached`、把零分母映射成 `null`、把触警信号的留痕与误报率计算交给服务层
`app/services/signal_audit`。不重算比率、不另立分子分母、不调模型；写入仅限
触警留痕（审计行），其去重与形状逻辑全部在服务层，本层不碰审计表。

口径要点（`app/core/online_signals.yaml`）：

- §2 通用规则：比率已在采集层 `round(..., 4)`，出口只搬运（裁定：不重算、
  不二次 round）；租户边界由采集层承担，本层不放宽任何鉴权。
- §2-B 裁定 11：`denominator <= 0` 时**出口层**把该行 `metric_value` 置 `null`、
  `breached` 置 `false`。既有会话级指标端点的 `0.0` 读数不改动——出口层映射，
  不是第二份事实源。
- §6-1 红线：`action_suggestion` 逐字取自阈值表，不拼接业务数据、不生成文案。
- §5：误报率的分子分母来自复核留痕；复核行的消费（误报率三态）与触警留痕的
  写入都在服务层 `app/services/signal_audit`，本层只调用其公开函数、不直接
  触碰审计表。本层经服务层公开函数写**两类留痕**——只读端点读到触警时幂等写
  breached 行，复核端点 `POST /api/v1/monitor/reviews` 写 reviewed 行。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_policy, get_session
from app.core.errors import ForbiddenError
from app.core.online_signals import get_online_signals
from app.core.policy import PolicyEngine
from app.core.security import Identity, get_identity
from app.schemas.monitor import (
    MonitorReviewRequest,
    MonitorReviewResponse,
    MonitorSignalRow,
    MonitorSignalsResponse,
)
from app.services.signal_audit import (
    false_positive_rate,
    record_breach,
    record_review,
)
from app.services.signal_readings import SignalReading, collect_readings

router = APIRouter(prefix="/api/v1/monitor", tags=["monitor"])

_STAFF_ROLES = ("approver", "support")

MAX_WINDOW_MINUTES = 60 * 24 * 90


def _require_staff(identity: Identity) -> None:
    if identity.has_role("admin") or any(
        identity.has_role(role) for role in _STAFF_ROLES
    ):
        return
    raise ForbiddenError("Staff role required.")


def _exit_metric(reading: SignalReading) -> float | None:
    """出口层的零分母映射（§2-B 裁定 11）。

    采集层对信号 B 零分母原样透传 `0.0`（不改采集层，也不改既有会话级指标），
    所以 `0.0` 伪读数在这里、只在这里变成 `null`。分母为 0 时不输出读数。
    """
    if reading.denominator <= 0:
        return None
    return reading.metric_value


@router.get("/signals", response_model=MonitorSignalsResponse)
def list_signals(
    window_minutes: int | None = Query(default=None, ge=1, le=MAX_WINDOW_MINUTES),
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    policy: PolicyEngine = Depends(get_policy),
) -> MonitorSignalsResponse:
    """Tenant-scoped online signal readings for the operations dashboard.

    三行固定顺序（`no_evidence_rate` / `handoff_rate` / `tool_failure_rate`），
    每行给出窗口、分子、分母、比率、阈值、是否触警、是否已基线校准与动作建议。
    `window_minutes` 只界定测量窗口，不给就是全量；`breached` 用**严格大于**
    阈值判定，等于阈值不算触警。零分母的行输出 `null` 且 `breached` 为 `false`。

    读取端点：不改配置、不调模型；读到触警的信号时经服务层幂等写一条审计
    留痕（口径 §5 去重），误报率按本租户 reviewed 行三态计算（已复核数为 0
    输出 `null`，与 `0.0` 可分）。
    """
    _require_staff(identity)
    table = get_online_signals()

    rows: list[MonitorSignalRow] = []
    breach_written = False
    for reading in collect_readings(
        session, identity, policy=policy, window_minutes=window_minutes
    ):
        # 未命中的 signal_id 是契约破坏，让它按原样抛 KeyError，不降级。
        signal = table.by_id(reading.signal_id)
        metric_value = _exit_metric(reading)
        row = MonitorSignalRow(
            signal_id=reading.signal_id,
            window_minutes=reading.window_minutes,
            numerator=reading.numerator,
            denominator=reading.denominator,
            metric_value=metric_value,
            threshold=signal.threshold,
            breached=metric_value is not None and metric_value > signal.threshold,
            calibrated=signal.calibrated,
            action_suggestion=signal.action_suggestion,
        )
        rows.append(row)
        # `breached` 为真已蕴含读数非 None；此处再判一次只为收窄类型。
        if row.breached and metric_value is not None:
            written = record_breach(
                session,
                identity,
                signal_id=reading.signal_id,
                window_minutes=window_minutes,
                metric_value=metric_value,
            )
            breach_written = breach_written or written

    if breach_written:
        # 服务层沿用「只 flush 不 commit」契约，事务边界在本层（照
        # `handoffs.py`/`analytics.py` 先例）。
        session.commit()

    return MonitorSignalsResponse(
        signals=rows,
        false_positive_rate=false_positive_rate(
            session, identity, window_minutes=window_minutes
        ),
    )


@router.post("/reviews", response_model=MonitorReviewResponse)
def review_signal(
    body: MonitorReviewRequest,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> MonitorReviewResponse:
    """记录一次人工复核，写一条 reviewed 审计留痕（口径 §4-7）。

    写入端点：鉴权沿用 `_require_staff` 形（与本层读端点同形，不因写放宽）。
    本函数体**只调用服务层公开函数** `record_review`——结论词表与 `signal_id`
    封闭词表的校验、审计行的形状与「只 flush 不 commit」都在服务层，非法输入
    在写入前即抛校验错误（HTTP 422）、不写任何行；只有校验通过才在此提交事务
    （提交归 API 层，照 `breach_written` → `session.commit()` 先例）。

    响应是受理回执，逐字回显被受理的复核三要素；不返回信号表、不重算误报率
    （误报率仍由只读端点单独可读，避免第二份读数路径）。零模型调用、不改配置。
    """
    _require_staff(identity)
    record_review(
        session,
        identity,
        signal_id=body.signal_id,
        conclusion=body.conclusion,
        window_minutes=body.window_minutes,
    )
    session.commit()
    return MonitorReviewResponse(
        signal_id=body.signal_id,
        conclusion=body.conclusion,
        window_minutes=body.window_minutes,
    )
