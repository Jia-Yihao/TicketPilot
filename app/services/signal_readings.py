"""三类在线信号的采集层：只读库计数，产出确定性读数，不做任何判定。

口径见 `app/core/online_signals.yaml`。每个信号只有一个真值
来源（裁定 2），分子/分母/时间列/租户列逐字照口径表；判定（与阈值比较）、HTTP 端点、
审计留痕、误报率都不在本模块——那是出口层的事。零模型调用：全程 SQL 计数。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.policy import PolicyEngine
from app.core.security import Identity
from app.core.time import utcnow
from app.models.conversation import Conversation, ConversationMessage
from app.models.enums import HandoffReason, ToolExecutionStatus
from app.models.handoff import HandoffEvent
from app.models.task import Task
from app.models.tool_execution import ToolExecution
from app.services.handoff import HandoffMetrics

SIGNAL_IDS: tuple[str, ...] = ("no_evidence_rate", "handoff_rate", "tool_failure_rate")

# no_evidence 意图集合的代码内抄本，抄自 app/agent/escalation.yaml::no_evidence.intents；
# 一致性由 tests/test_signal_readings.py 现读 YAML 比对钉死（口径 §9-3）。
NO_EVIDENCE_INTENTS: tuple[str, ...] = ("POLICY_QA", "SELF_ANALYTICS")

# 信号 C 分母：已落定的执行态。排除 PENDING（尚未执行，不计入任何一侧）。
# UNKNOWN 进分母不进分子——它是「外部结果未知」，失败率因此保守偏低（口径 §10-2）。
_SETTLED_STATUSES: tuple[ToolExecutionStatus, ...] = (
    ToolExecutionStatus.SENT,
    ToolExecutionStatus.RECONCILED,
    ToolExecutionStatus.FAILED,
    ToolExecutionStatus.CANCELLED,
    ToolExecutionStatus.UNKNOWN,
)


@dataclass(frozen=True, slots=True)
class SignalReading:
    signal_id: str
    window_minutes: int | None
    numerator: int
    denominator: int
    metric_value: float | None


def _since(window_minutes: int | None) -> datetime | None:
    if window_minutes is None:
        return None
    return utcnow() - timedelta(minutes=window_minutes)


def _ratio(numerator: int, denominator: int) -> float | None:
    # A/C 零分母输出 None（口径 §2 通用规则：不落 0.0 伪读数）。
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def no_evidence_reading(
    session: Session, identity: Identity, *, window_minutes: int | None = None
) -> SignalReading:
    since = _since(window_minutes)

    numerator_query = select(func.count()).select_from(HandoffEvent).where(
        HandoffEvent.tenant_id == identity.tenant_id,
        HandoffEvent.reason == HandoffReason.NO_EVIDENCE.value,
    )
    if since is not None:
        numerator_query = numerator_query.where(HandoffEvent.created_at >= since)
    numerator = int(session.scalar(numerator_query) or 0)

    denominator_query = (
        select(func.count())
        .select_from(ConversationMessage)
        .join(Conversation, ConversationMessage.conversation_id == Conversation.id)
        .where(
            Conversation.tenant_id == identity.tenant_id,
            ConversationMessage.role == "user",
            ConversationMessage.intent.in_(NO_EVIDENCE_INTENTS),
        )
    )
    if since is not None:
        denominator_query = denominator_query.where(ConversationMessage.created_at >= since)
    denominator = int(session.scalar(denominator_query) or 0)

    return SignalReading(
        signal_id="no_evidence_rate",
        window_minutes=window_minutes,
        numerator=numerator,
        denominator=denominator,
        metric_value=_ratio(numerator, denominator),
    )


def handoff_reading(
    session: Session,
    identity: Identity,
    *,
    policy: PolicyEngine,
    window_minutes: int | None = None,
) -> SignalReading:
    # 逐字复用既有 HandoffMetrics.collect() 读数（裁定 2：不另立第二套分子分母）。
    # 零分母时 collect() 的 _rate() 返回 0.0，采集层原样透传，不改置 None；
    # monitor 出口的 null 映射是出口层职责（口径 §2-B 裁定 11、§9-14）。
    metrics = HandoffMetrics(session, policy=policy).collect(
        identity, window_minutes=window_minutes
    )
    return SignalReading(
        signal_id="handoff_rate",
        window_minutes=window_minutes,
        numerator=int(metrics["handoff_conversations"]),
        denominator=int(metrics["conversations"]),
        metric_value=float(metrics["handoff_rate"]),
    )


def tool_failure_reading(
    session: Session, identity: Identity, *, window_minutes: int | None = None
) -> SignalReading:
    since = _since(window_minutes)
    # tool_executions 无 tenant_id 列，租户边界必须 JOIN tasks（口径 §2-C 硬约束）。
    base_query = (
        select(func.count())
        .select_from(ToolExecution)
        .join(Task, ToolExecution.task_id == Task.id)
        .where(Task.tenant_id == identity.tenant_id)
    )
    numerator_query = base_query.where(
        ToolExecution.status == ToolExecutionStatus.FAILED.value
    )
    denominator_query = base_query.where(
        ToolExecution.status.in_([status.value for status in _SETTLED_STATUSES])
    )
    if since is not None:
        numerator_query = numerator_query.where(ToolExecution.created_at >= since)
        denominator_query = denominator_query.where(ToolExecution.created_at >= since)

    numerator = int(session.scalar(numerator_query) or 0)
    denominator = int(session.scalar(denominator_query) or 0)

    return SignalReading(
        signal_id="tool_failure_rate",
        window_minutes=window_minutes,
        numerator=numerator,
        denominator=denominator,
        metric_value=_ratio(numerator, denominator),
    )


def collect_readings(
    session: Session,
    identity: Identity,
    *,
    policy: PolicyEngine,
    window_minutes: int | None = None,
) -> list[SignalReading]:
    # 固定顺序，与 SIGNAL_IDS 逐一对应（口径 §2）。
    return [
        no_evidence_reading(session, identity, window_minutes=window_minutes),
        handoff_reading(session, identity, policy=policy, window_minutes=window_minutes),
        tool_failure_reading(session, identity, window_minutes=window_minutes),
    ]
