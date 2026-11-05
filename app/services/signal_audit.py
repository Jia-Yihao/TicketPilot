"""三类在线信号的触警留痕、复核留痕与误报率服务层（口径 §4-7/§5，tier 4）。

两条审计动作名、`resource_type` 与 reviewed 结论值域在本模块**单一定义**
（口径 §5 表 + 「值域只有一个产出点」），
实现轮不得改名。本模块是这两条审计动作唯一的**写入生产者**——`record_breach`
幂等写 breached 行、`record_review` 写 reviewed 行——与误报率三态读数都只按审计
台账的**具体列**等值比较读写，**从不解析 `detail` JSON**（口径 §1.0-7，与
`traces.py` 纪律同向）。

出口层 `app/api/v1/monitor.py` 只调用本模块的公开函数，不直接触碰审计模型或
仓储（由 `tests/test_monitor_api.py` 的 `inspect.getsource` 用例钉住）。
写入沿用 `AuditRepository.record` 的「只 flush 不 commit」契约——事务提交归
API 层（`handoffs.py`/`analytics.py` 先例）。零模型调用、不写任何配置文件。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ValidationAppError
from app.core.online_signals import get_online_signals
from app.core.security import Identity
from app.core.time import utcnow
from app.models.audit import AuditEvent
from app.repositories.audit import AuditRepository

# 口径 §5 字面定死的动作名（均 23 字符，满足 action String(64)）。
BREACHED_ACTION = "monitor.signal.breached"
REVIEWED_ACTION = "monitor.signal.reviewed"

# 口径 §5 资源类型（14 字符，满足 resource_type String(32)）。
SIGNAL_RESOURCE_TYPE = "monitor_signal"

# reviewed 的结论值域（≤16 字符，满足 outcome String(16)）：封闭词表，
# 值只从这一处产出；词表之外的字面不属于本契约。
FALSE_POSITIVE_OUTCOME = "FALSE_POSITIVE"
CONFIRMED_OUTCOME = "CONFIRMED"
REVIEW_OUTCOMES: frozenset[str] = frozenset(
    {FALSE_POSITIVE_OUTCOME, CONFIRMED_OUTCOME}
)

# breached 行的 outcome：既有词表字面（先例 app/services/handoff.py:354），
# 「触警」语义由动作名承担，不发明新值。
BREACH_OUTCOME = "SUCCESS"


def _since(window_minutes: int | None) -> datetime | None:
    if window_minutes is None:
        return None
    return utcnow() - timedelta(minutes=window_minutes)


def _already_breached(
    session: Session,
    tenant_id: str,
    signal_id: str,
    *,
    window_minutes: int | None,
    realert_minutes: int,
) -> bool:
    """幂等去重判定（口径 §5 两条件，只用具体列，从不解析 `detail`）。

    条件一：存在 `created_at >=` 本次读取窗口起点的行——全量窗口
    （`window_minutes=None`）下任何历史行都在窗口内，等价于「有行即不重写」；
    条件二：距最近一条 breached 行不足 `realert_minutes`。两个条件都化成
    「最近一条是否已越过两个截止时刻中更早的那个」，一次计数查询判完。
    """
    stmt = select(func.count()).select_from(AuditEvent).where(
        AuditEvent.tenant_id == tenant_id,
        AuditEvent.action == BREACHED_ACTION,
        AuditEvent.resource_type == SIGNAL_RESOURCE_TYPE,
        AuditEvent.resource_id == signal_id,
    )
    window_start = _since(window_minutes)
    if window_start is None:
        cutoff = None  # 全量窗口：无需时间过滤，有行即命中条件一
    else:
        cutoff = min(window_start, utcnow() - timedelta(minutes=realert_minutes))
    if cutoff is not None:
        stmt = stmt.where(AuditEvent.created_at >= cutoff)
    return int(session.scalar(stmt) or 0) > 0


def record_breach(
    session: Session,
    identity: Identity,
    *,
    signal_id: str,
    window_minutes: int | None,
    metric_value: float,
) -> bool:
    """为一次触警写一条 breached 留痕；返回本次是否真的写了。

    阈值与 `realert_minutes` 都取自 YAML 行对象（`get_online_signals()`），
    调用方不得自行硬编码。`detail` 恰含口径 §5 的封闭四键
    `{signal_id, window_minutes, metric_value, threshold}`——禁入客户文案、
    单号、原始 span/属性值（台账他人可读，纪律同「只记计数，不记单号」）。
    """
    signal = get_online_signals().by_id(signal_id)
    if _already_breached(
        session,
        identity.tenant_id,
        signal_id,
        window_minutes=window_minutes,
        realert_minutes=signal.realert_minutes,
    ):
        return False
    AuditRepository(session).record(
        actor_id=identity.user_id,
        tenant_id=identity.tenant_id,
        action=BREACHED_ACTION,
        resource_type=SIGNAL_RESOURCE_TYPE,
        resource_id=signal_id,
        outcome=BREACH_OUTCOME,
        detail={
            "signal_id": signal_id,
            "window_minutes": window_minutes,
            "metric_value": metric_value,
            "threshold": signal.threshold,
        },
    )
    return True


def record_review(
    session: Session,
    identity: Identity,
    *,
    signal_id: str,
    conclusion: str,
    window_minutes: int | None = None,
) -> None:
    """为一次人工复核写一条 reviewed 留痕（口径 §4-7/§5，tier 4）。

    校验先于写入，且不吞异常、不静默降级：

    - `conclusion` 必须落在本模块单一定义的封闭词表 `REVIEW_OUTCOMES` 内，
      否则复用既有校验错误 `ValidationAppError`（HTTP 422），**不写任何行**
      （口径 task-order §5「值域只有一个产出点」，不在词表外的字面不属于本契约）。
    - `signal_id` 必须是阈值表里的信号——用 `get_online_signals()` 这一唯一
      词表来源校验，**不另立字面清单**；请求体里的未知 `signal_id` 属外部输入，
      把它解析出的 `KeyError` 收敛为 `ValidationAppError`，不冒泡成 500。

    写入沿用 `AuditRepository.record` 的「只 flush 不 commit」契约——事务提交归
    API 层。`actor_id`/`tenant_id` 取复核坐席身份；`resource_id` 是被复核信号的
    `signal_id`；`outcome` 即复核结论；`detail` 恰含口径 §5 的封闭两键
    `{signal_id, window_minutes}`——禁入客户文案、单号、原始读数。复核不绑定
    具体某条 breached 行，被复核的是「窗口 × 信号」这一判定，故**无幂等去重**：
    每次复核各留一行，误报率按具体列现算。零模型调用、不写任何配置文件。
    """
    if conclusion not in REVIEW_OUTCOMES:
        raise ValidationAppError(
            "Unknown review conclusion.",
            details={"allowed": sorted(REVIEW_OUTCOMES)},
        )
    try:
        get_online_signals().by_id(signal_id)
    except KeyError as exc:
        raise ValidationAppError(
            "Unknown signal_id for review.",
            details={
                "allowed": sorted(
                    signal.signal_id for signal in get_online_signals().signals
                )
            },
        ) from exc
    AuditRepository(session).record(
        actor_id=identity.user_id,
        tenant_id=identity.tenant_id,
        action=REVIEWED_ACTION,
        resource_type=SIGNAL_RESOURCE_TYPE,
        resource_id=signal_id,
        outcome=conclusion,
        detail={"signal_id": signal_id, "window_minutes": window_minutes},
    )


def false_positive_rate(
    session: Session,
    identity: Identity,
    *,
    window_minutes: int | None = None,
) -> float | None:
    """误报率三态（口径 §5 公式 + §9-8）：null / 0.0 / round(x, 4)。

    `count(reviewed 且 outcome=FALSE_POSITIVE) ÷ count(reviewed)`，同租户、
    同窗口；分子分母都是具体列等值比较，不解析 `detail`。已复核数为 0 时
    返回 `None`（JSON `null`），**不落 `0.0`**——「没复核过」与「复核过且
    全对」必须可分。
    """
    since = _since(window_minutes)

    reviewed_query = select(func.count()).select_from(AuditEvent).where(
        AuditEvent.tenant_id == identity.tenant_id,
        AuditEvent.action == REVIEWED_ACTION,
        AuditEvent.resource_type == SIGNAL_RESOURCE_TYPE,
    )
    false_query = reviewed_query.where(AuditEvent.outcome == FALSE_POSITIVE_OUTCOME)
    if since is not None:
        reviewed_query = reviewed_query.where(AuditEvent.created_at >= since)
        false_query = false_query.where(AuditEvent.created_at >= since)

    reviewed = int(session.scalar(reviewed_query) or 0)
    if reviewed == 0:
        return None
    false_positives = int(session.scalar(false_query) or 0)
    return round(false_positives / reviewed, 4)
