"""运维信号出口：三类在线信号的只读 HTTP 端点（口径 §4-1/§4-4）。

这一层**只做三件事**：把采集层的读数逐字搬出来、把阈值判定算成 `breached`、
把零分母映射成 `null`。不重算比率、不另立分子分母、不写任何数据、不调模型。

口径要点（`.qoder/rules/online-signals-and-notification.md`）：

- §2 通用规则：比率已在采集层 `round(..., 4)`，出口只搬运（裁定：不重算、
  不二次 round）；租户边界由采集层承担，本层不放宽任何鉴权。
- §2-B 裁定 11：`denominator <= 0` 时**出口层**把该行 `metric_value` 置 `null`、
  `breached` 置 `false`。既有会话级指标端点的 `0.0` 读数不改动——出口层映射，
  不是第二份事实源。
- §6-1 红线：`action_suggestion` 逐字取自阈值表，不拼接业务数据、不生成文案。
- §5：误报率的分子分母来自复核留痕，其生产者不在本任务；本层恒 emit `null`，
  且**不读审计台账**。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_policy, get_session
from app.core.errors import ForbiddenError
from app.core.online_signals import get_online_signals
from app.core.policy import PolicyEngine
from app.core.security import Identity, get_identity
from app.schemas.monitor import MonitorSignalRow, MonitorSignalsResponse
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

    只读端点：不写审计、不改配置、不调模型；误报率因无复核生产者而恒为 `null`。
    """
    _require_staff(identity)
    table = get_online_signals()

    rows: list[MonitorSignalRow] = []
    for reading in collect_readings(
        session, identity, policy=policy, window_minutes=window_minutes
    ):
        # 未命中的 signal_id 是契约破坏，让它按原样抛 KeyError，不降级。
        signal = table.by_id(reading.signal_id)
        metric_value = _exit_metric(reading)
        rows.append(
            MonitorSignalRow(
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
        )

    return MonitorSignalsResponse(signals=rows, false_positive_rate=None)
