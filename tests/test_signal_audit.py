"""R11 T5-a：服务层触警留痕、幂等去重与误报率三态的用例。

口径来源：`.qoder/rules/online-signals-and-notification.md` §5（动作名、
`resource_type`、breached 行契约、幂等去重两条件、detail 封闭键集、误报率公式
与三态）。断言对字面口径，不对实现自证：动作名与结论值一律用字面量写死，
从实现导入的符号只用于「词表封闭且不得改名」这一条钉桩。

本文件既保留「直接走 ORM 种 reviewed 审计行」来校验服务层消费侧（误报率三态、
幂等去重），又对 reviewed 行的**生产者** `record_review`（口径 §4-7/§5）加契约
用例。reviewed 行的写入端点 `POST /api/v1/monitor/reviews` 已随本任务落地，其
端到端形态由 `tests/test_monitor_api.py` 覆盖。时间推进用显式 `created_at`
控制，不 monkeypatch 时钟。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.core.errors import ValidationAppError
from app.core.security import Identity
from app.core.time import utcnow
from app.models.audit import AuditEvent
from app.services.signal_audit import (
    BREACHED_ACTION,
    REVIEW_OUTCOMES,
    REVIEWED_ACTION,
    SIGNAL_RESOURCE_TYPE,
    false_positive_rate,
    record_breach,
    record_review,
)

NO_EVIDENCE = "no_evidence_rate"
HANDOFF = "handoff_rate"


def _identity(tenant: str) -> Identity:
    return Identity(tenant, "op-1", frozenset({"support"}))


def _seed(
    session: Any,
    tenant: str,
    *,
    action: str,
    resource_id: str,
    outcome: str,
    age_minutes: int = 0,
) -> None:
    session.add(
        AuditEvent(
            id=f"aud_{uuid.uuid4().hex[:12]}_{action[-3:]}_{age_minutes}",
            actor_id="op-1",
            tenant_id=tenant,
            action=action,
            resource_type="monitor_signal",
            resource_id=resource_id,
            outcome=outcome,
            detail={},
            created_at=utcnow() - timedelta(minutes=age_minutes),
        )
    )


def _count(session: Any, tenant: str, action: str, resource_id: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.tenant_id == tenant,
                AuditEvent.action == action,
                AuditEvent.resource_id == resource_id,
            )
        )
        or 0
    )


# -------------------------------------------- 1. 词表单一定义处（§5 表 + 列宽）


def test_action_vocabulary_is_closed_and_fits_the_columns() -> None:
    # 口径 §5：动作名 23 字符（action String(64)）、resource_type 14 字符
    # （String(32)）、结论值 ≤16 字符（outcome String(16)）——实现轮不得改名。
    assert BREACHED_ACTION == "monitor.signal.breached"
    assert REVIEWED_ACTION == "monitor.signal.reviewed"
    assert SIGNAL_RESOURCE_TYPE == "monitor_signal"
    assert REVIEW_OUTCOMES == frozenset({"FALSE_POSITIVE", "CONFIRMED"})
    assert len(BREACHED_ACTION) <= 64 and len(REVIEWED_ACTION) <= 64
    assert len(SIGNAL_RESOURCE_TYPE) <= 32
    assert all(len(outcome) <= 16 for outcome in REVIEW_OUTCOMES)


# ---------------------------------------------------- 2. breached 行契约与幂等


def test_record_breach_writes_exactly_the_contracted_row(app: FastAPI) -> None:
    tenant = "iso-sa-write"
    with app.state.session_factory() as session:
        wrote = record_breach(
            session,
            _identity(tenant),
            signal_id=HANDOFF,
            window_minutes=60,
            metric_value=0.8,
        )
        assert wrote is True
        event = session.scalars(
            select(AuditEvent).where(
                AuditEvent.tenant_id == tenant,
                AuditEvent.action == "monitor.signal.breached",
            )
        ).one()
        assert event.actor_id == "op-1"
        assert event.resource_type == "monitor_signal"
        assert event.resource_id == HANDOFF
        assert event.outcome == "SUCCESS"
        # detail 恰含四键（§5 封闭键集），值就是本次读取的四个数。
        assert event.detail == {
            "signal_id": HANDOFF,
            "window_minutes": 60,
            "metric_value": 0.8,
            "threshold": 0.55,
        }

        # 同窗口第二次读取：幂等去重，不再写。负对照——不去重的实现这里出 2 行。
        assert (
            record_breach(
                session,
                _identity(tenant),
                signal_id=HANDOFF,
                window_minutes=60,
                metric_value=0.9,
            )
            is False
        )
        session.commit()

    with app.state.session_factory() as session:
        assert _count(session, tenant, "monitor.signal.breached", HANDOFF) == 1


def test_record_breach_dedups_whole_history_window(app: FastAPI) -> None:
    # `window_minutes=None` 即全量窗口：任何历史 breached 行都落在窗口内，
    # §5 条件一（created_at >= 窗口起点）对全量窗口恒真——有旧行即不重写。
    tenant = "iso-sa-fulldedup"
    with app.state.session_factory() as session:
        _seed(
            session,
            tenant,
            action="monitor.signal.breached",
            resource_id=NO_EVIDENCE,
            outcome="SUCCESS",
            age_minutes=3 * 24 * 60,
        )
        session.commit()
        wrote = record_breach(
            session,
            _identity(tenant),
            signal_id=NO_EVIDENCE,
            window_minutes=None,
            metric_value=0.5,
        )
        assert wrote is False
        assert _count(session, tenant, "monitor.signal.breached", NO_EVIDENCE) == 1


def test_record_breach_skips_within_realert_even_outside_the_window(
    app: FastAPI,
) -> None:
    # 旧行 10 分钟前：在 window_minutes=5 之外（条件一不拦），但在
    # realert_minutes=15 之内（条件二拦下）——钉住去重的第二条件单独生效。
    tenant = "iso-sa-realert"
    with app.state.session_factory() as session:
        _seed(
            session,
            tenant,
            action="monitor.signal.breached",
            resource_id=NO_EVIDENCE,
            outcome="SUCCESS",
            age_minutes=10,
        )
        session.commit()
        wrote = record_breach(
            session,
            _identity(tenant),
            signal_id=NO_EVIDENCE,
            window_minutes=5,
            metric_value=0.3,
        )
        assert wrote is False
        assert _count(session, tenant, "monitor.signal.breached", NO_EVIDENCE) == 1


def test_record_breach_writes_when_past_window_and_realert(app: FastAPI) -> None:
    # 旧行 20 分钟前：既在 window_minutes=5 之外，也超出 realert_minutes=15——
    # 两个条件都不拦，必须写新行（§9-9「跨过再 breach 有新行」）。
    tenant = "iso-sa-renew"
    with app.state.session_factory() as session:
        _seed(
            session,
            tenant,
            action="monitor.signal.breached",
            resource_id=NO_EVIDENCE,
            outcome="SUCCESS",
            age_minutes=20,
        )
        session.commit()
        wrote = record_breach(
            session,
            _identity(tenant),
            signal_id=NO_EVIDENCE,
            window_minutes=5,
            metric_value=0.3,
        )
        assert wrote is True
        assert _count(session, tenant, "monitor.signal.breached", NO_EVIDENCE) == 2


def test_record_breach_is_scoped_per_signal_and_tenant(app: FastAPI) -> None:
    # 去重按「同租户、同 resource_id」：别的信号、别的租户都不拦本行。
    mine = "iso-sa-scope-a"
    other = "iso-sa-scope-b"
    with app.state.session_factory() as session:
        _seed(
            session,
            mine,
            action="monitor.signal.breached",
            resource_id=NO_EVIDENCE,
            outcome="SUCCESS",
        )
        _seed(
            session,
            other,
            action="monitor.signal.breached",
            resource_id=HANDOFF,
            outcome="SUCCESS",
        )
        session.commit()
        assert (
            record_breach(
                session,
                _identity(mine),
                signal_id=HANDOFF,
                window_minutes=60,
                metric_value=0.9,
            )
            is True
        )
        assert (
            record_breach(
                session,
                _identity(other),
                signal_id=HANDOFF,
                window_minutes=60,
                metric_value=0.9,
            )
            is False
        )


# --------------------------------------------------------- 3. 误报率三态（§5/§9-8）


def test_false_positive_rate_zero_reviewed_is_none_not_zero(app: FastAPI) -> None:
    with app.state.session_factory() as session:
        rate = false_positive_rate(
            session, _identity("iso-sa-none"), window_minutes=None
        )
        assert rate is None  # 负对照：落 0.0 的实现这里即红——「没复核过」必须可分


def test_false_positive_rate_all_confirmed_is_zero_float(app: FastAPI) -> None:
    tenant = "iso-sa-zero"
    with app.state.session_factory() as session:
        _seed(
            session,
            tenant,
            action="monitor.signal.reviewed",
            resource_id=HANDOFF,
            outcome="CONFIRMED",
        )
        _seed(
            session,
            tenant,
            action="monitor.signal.reviewed",
            resource_id=NO_EVIDENCE,
            outcome="CONFIRMED",
        )
        session.commit()
        rate = false_positive_rate(session, _identity(tenant), window_minutes=None)
        assert rate == 0.0
        assert isinstance(rate, float)  # 与上面的 None 两条各自成立才算「可分」


def test_false_positive_rate_mixed_is_rounded_to_four(app: FastAPI) -> None:
    tenant = "iso-sa-mixed"
    outcomes = ("FALSE_POSITIVE", "FALSE_POSITIVE", "CONFIRMED", "CONFIRMED",
                "CONFIRMED", "CONFIRMED", "CONFIRMED")
    with app.state.session_factory() as session:
        for outcome in outcomes:
            _seed(
                session,
                tenant,
                action="monitor.signal.reviewed",
                resource_id=HANDOFF,
                outcome=outcome,
            )
        session.commit()
        rate = false_positive_rate(session, _identity(tenant), window_minutes=None)
        assert rate == round(2 / 7, 4)  # 0.2857：口径 §2 通用规则 round(x, 4)


def test_false_positive_rate_counts_only_reviewed_rows(app: FastAPI) -> None:
    # breached 行不得进分母——只数 `monitor.signal.reviewed`（具体列等值）。
    tenant = "iso-sa-actions"
    with app.state.session_factory() as session:
        _seed(
            session,
            tenant,
            action="monitor.signal.reviewed",
            resource_id=HANDOFF,
            outcome="FALSE_POSITIVE",
        )
        _seed(
            session,
            tenant,
            action="monitor.signal.breached",
            resource_id=HANDOFF,
            outcome="SUCCESS",
        )
        session.commit()
        rate = false_positive_rate(session, _identity(tenant), window_minutes=None)
        assert rate == 1.0  # 分母 1（若 breached 行混入分母会变成 0.5）


def test_false_positive_rate_is_window_and_tenant_scoped(app: FastAPI) -> None:
    mine = "iso-sa-window-mine"
    theirs = "iso-sa-window-theirs"
    with app.state.session_factory() as session:
        _seed(
            session,
            mine,
            action="monitor.signal.reviewed",
            resource_id=HANDOFF,
            outcome="FALSE_POSITIVE",
            age_minutes=120,
        )
        _seed(
            session,
            theirs,
            action="monitor.signal.reviewed",
            resource_id=HANDOFF,
            outcome="FALSE_POSITIVE",
        )
        session.commit()

        # 同租户：行在 60 分钟窗口外 → 已复核数 0 → None；全量窗口 → 0 被计入。
        assert (
            false_positive_rate(session, _identity(mine), window_minutes=60) is None
        )
        assert false_positive_rate(session, _identity(mine), window_minutes=None) == 1.0
        # 跨租户：A 租户的行不影响 B 租户（§9-13 相关，服务层同源断言）。
        assert (
            false_positive_rate(session, _identity("iso-sa-window-none"), window_minutes=None)
            is None
        )
        assert false_positive_rate(session, _identity(theirs), window_minutes=60) == 1.0


# -------------------------------------- 4. record_review 生产者契约（§4-7/§5/§9-8）


def test_record_review_writes_exactly_the_contracted_row(app: FastAPI) -> None:
    # reviewed 行契约逐字对：动作/资源类型/资源 id/结论/明细封闭两键，只 flush 不 commit
    # （提交归 API 层）——本用例在 API 提交前即可读到 flush 出的行，正说明未提交也可见。
    tenant = "iso-sa-review-write"
    with app.state.session_factory() as session:
        record_review(
            session,
            _identity(tenant),
            signal_id=HANDOFF,
            conclusion="CONFIRMED",
            window_minutes=60,
        )
        event = session.scalars(
            select(AuditEvent).where(
                AuditEvent.tenant_id == tenant,
                AuditEvent.action == "monitor.signal.reviewed",
            )
        ).one()
        assert event.actor_id == "op-1"
        assert event.resource_type == "monitor_signal"
        assert event.resource_id == HANDOFF
        assert event.outcome == "CONFIRMED"
        # detail 恰含口径 §5 的封闭两键——不落客户文案、单号、原始读数。
        assert set(event.detail) == {"signal_id", "window_minutes"}
        assert event.detail == {"signal_id": HANDOFF, "window_minutes": 60}
        session.commit()

    with app.state.session_factory() as session:
        assert _count(session, tenant, "monitor.signal.reviewed", HANDOFF) == 1


def test_record_review_default_window_is_none_but_still_contracted(app: FastAPI) -> None:
    # 不给 window_minutes 即全量窗口复核：detail.window_minutes 落 None，键集仍是两键。
    tenant = "iso-sa-review-nowin"
    with app.state.session_factory() as session:
        record_review(
            session,
            _identity(tenant),
            signal_id=NO_EVIDENCE,
            conclusion="FALSE_POSITIVE",
        )
        event = session.scalars(
            select(AuditEvent).where(
                AuditEvent.tenant_id == tenant,
                AuditEvent.action == "monitor.signal.reviewed",
            )
        ).one()
        assert event.outcome == "FALSE_POSITIVE"
        assert event.detail == {"signal_id": NO_EVIDENCE, "window_minutes": None}


def test_record_review_is_not_deduped_each_review_is_its_own_row(app: FastAPI) -> None:
    # §5：复核不绑定具体告警行、无幂等去重——同信号两次复核各留一行。
    # 负对照：若误加了 breached 式去重，这里只会有 1 行。
    tenant = "iso-sa-review-multi"
    with app.state.session_factory() as session:
        record_review(session, _identity(tenant), signal_id=HANDOFF, conclusion="CONFIRMED")
        record_review(
            session, _identity(tenant), signal_id=HANDOFF, conclusion="FALSE_POSITIVE"
        )
        session.commit()
        assert _count(session, tenant, "monitor.signal.reviewed", HANDOFF) == 2


def test_record_review_rejects_conclusion_outside_the_closed_set(app: FastAPI) -> None:
    # 词表外的结论必须显式失败（复用既有校验错误类型，不新造错误类），且不写任何行。
    tenant = "iso-sa-review-badconclusion"
    with app.state.session_factory() as session:
        for bad in ("false_positive", "MAYBE", ""):
            with pytest.raises(ValidationAppError):
                record_review(
                    session, _identity(tenant), signal_id=HANDOFF, conclusion=bad
                )
        assert _count(session, tenant, "monitor.signal.reviewed", HANDOFF) == 0
        assert (
            session.scalars(
                select(AuditEvent).where(AuditEvent.tenant_id == tenant)
            ).all()
            == []
        )


def test_record_review_rejects_unknown_signal_id_without_crashing(app: FastAPI) -> None:
    # 请求体里的 signal_id 属外部输入：按阈值表的封闭词表校验，未知即校验错误，
    # 不得让 by_id 的 KeyError 冒泡成 500，也不得写行。
    tenant = "iso-sa-review-badsignal"
    with app.state.session_factory() as session:
        with pytest.raises(ValidationAppError):
            record_review(
                session,
                _identity(tenant),
                signal_id="ghost_rate",
                conclusion="CONFIRMED",
            )
        assert _count(session, tenant, "monitor.signal.reviewed", "ghost_rate") == 0
        assert (
            session.scalars(
                select(AuditEvent).where(AuditEvent.tenant_id == tenant)
            ).all()
            == []
        )


def test_record_review_rows_drive_false_positive_rate(app: FastAPI) -> None:
    # 生产者写行 → 消费侧读数按 §5 公式串起 §4-7 与 §9-8 的三态（服务层同源）。
    tenant = "iso-sa-review-fpr"
    with app.state.session_factory() as session:
        assert (
            false_positive_rate(session, _identity(tenant), window_minutes=None) is None
        )
        record_review(session, _identity(tenant), signal_id=HANDOFF, conclusion="CONFIRMED")
        record_review(session, _identity(tenant), signal_id=HANDOFF, conclusion="CONFIRMED")
        assert (
            false_positive_rate(session, _identity(tenant), window_minutes=None) == 0.0
        )
        record_review(
            session, _identity(tenant), signal_id=HANDOFF, conclusion="FALSE_POSITIVE"
        )
        assert false_positive_rate(
            session, _identity(tenant), window_minutes=None
        ) == round(1 / 3, 4)


def test_record_review_is_scoped_to_its_own_tenant(app: FastAPI) -> None:
    # 复核行按租户落库：A 的复核不影响 B 的误报率（§9-13 相关，服务层断言）。
    mine = "iso-sa-review-mine"
    theirs = "iso-sa-review-theirs"
    with app.state.session_factory() as session:
        record_review(
            session, _identity(theirs), signal_id=HANDOFF, conclusion="FALSE_POSITIVE"
        )
        assert (
            false_positive_rate(session, _identity(mine), window_minutes=None) is None
        )
        assert (
            false_positive_rate(session, _identity(theirs), window_minutes=None) == 1.0
        )
