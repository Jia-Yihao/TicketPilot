"""R10 T5：把 ``evals/question_coverage.py`` 的关键判据钉住的测试侧用例。

口径 ``.qoder/rules/question-gate-coverage-and-channel-intent.md`` §3（T5 契约）与
``docs/下一步规划.md`` :73 的「T5 前置裁定」：

* 算术本体与 ``coverage_from_ledger`` 逐字同一（本文件只 import，不留第二套真相）；
* 纯离线、零模型调用、零写库——「无机器守护」的只读边界在这里机械化（判据 4）；
* 恒等式负数必须显式失败、且点名会话（判据 6）；
* 分母为 0 打印「不可计算（分母为 0）」，不抛、不伪造 0%（判据 5）；
* 报告只落计数，严禁出现客户原文（判据 7）。

所有用例都不 import ``app.agent.*``、不调模型；DB 侧用进程内 SQLite，
数据以 ORM 行直接构造，形态与生产落盘逐字同键（``gate``/``model``/``source``）。
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import build_engine, build_session_factory
from app.models.conversation import Conversation, ConversationMessage
from app.models.llm_usage import LlmUsageRecord
from app.models.observability import TraceSpan
from evals import question_coverage
from evals.question_coverage import (
    EXIT_CONSULT,
    EXIT_DETAIL_FOLLOWUP,
    EXIT_QUESTION,
    EXIT_RULES_HIT,
    SHAPE_QUESTION,
    SHAPE_STATEMENT,
    SHAPE_TEXT_UNAVAILABLE,
    build_report,
    classify_shape,
    exit_of_gap,
    pair_request_texts,
)

_BASE_TIME = datetime(2026, 10, 29, 8, 0, 0, tzinfo=UTC).replace(tzinfo=None)


def _message(
    role: str, content: str, payload: dict[str, Any] | None = None, *, order: int = 0
) -> ConversationMessage:
    return ConversationMessage(
        role=role,
        content=content,
        payload=payload or {},
        created_at=_BASE_TIME + timedelta(seconds=order),
    )


def _fresh_session() -> Session:
    engine = build_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return build_session_factory(engine)()


def _classify_span(index: int, gap: dict[str, Any] | None, conversation: str) -> TraceSpan:
    return TraceSpan(
        id=f"span-{conversation}-{index}",
        trace_id=f"T{index}",
        tenant_id="demo",
        kind="understand",
        name="support.classify",
        conversation_id=conversation,
        attributes={"model_gap": gap},
    )


# ------------------------------------------------------------------ 判据 1：形态判定
def test_classify_shape_maps_the_three_shapes() -> None:
    assert classify_shape(None) == SHAPE_TEXT_UNAVAILABLE
    assert classify_shape("运费谁出？") == SHAPE_QUESTION
    assert classify_shape("where is my order?") == SHAPE_QUESTION
    assert classify_shape("我申请退一部分款") == SHAPE_STATEMENT
    assert classify_shape("") == SHAPE_STATEMENT


# ------------------------------------------------------------------ 判据 2：出口值域封闭
def test_exit_of_gap_closes_its_value_domain() -> None:
    assert exit_of_gap(None) == EXIT_RULES_HIT
    # rules_hit 列名必须如实带上「与 rules 模式不可分辨」的声明，不得谎称就是 rules_hit。
    assert EXIT_RULES_HIT.startswith("rules_hit") and "不可分辨" in EXIT_RULES_HIT
    assert exit_of_gap({"gate": "question"}) == EXIT_QUESTION
    assert exit_of_gap({"gate": "consult"}) == EXIT_CONSULT
    assert exit_of_gap({"gate": "detail_followup"}) == EXIT_DETAIL_FOLLOWUP
    with pytest.raises(ValueError):
        exit_of_gap({"gate": "made_up"})
    with pytest.raises(ValueError):
        exit_of_gap({})


# ------------------------------------------------------------------ 判据 3：请求文本配对
def test_pairing_overwrites_and_tolerates_dangling_user() -> None:
    # 覆盖语义：连续多条 user 时取最后一条（照抄 evals/replay.py:172-186 的形状）。
    assert pair_request_texts([
        _message("user", "第一条"),
        _message("user", "第二条"),
        _message("assistant", "回", {"trace_id": "T1"}),
    ]) == {"T1": "第二条"}

    # 静默回合（SupportAgent._silent）只写 user 行、不写 assistant 行：不报错、不产轮次。
    assert pair_request_texts([_message("user", "客户补了一句")]) == {}

    # 缺 trace_id 的轮次一律不进映射（该轮在报告里是「文本不可得」，不做位置兜底）。
    assert pair_request_texts([
        _message("user", "问一问？"),
        _message("assistant", "答"),
    ]) == {}

    # 同一个 trace 被配对两次 = 配对写错，必须显式失败。
    with pytest.raises(ValueError):
        pair_request_texts([
            _message("user", "一"),
            _message("assistant", "答", {"trace_id": "T"}),
            _message("user", "二"),
            _message("assistant", "答", {"trace_id": "T"}),
        ])


# ------------------------------------------------------------ 判据 4：只读纪律机械化
def test_report_module_stays_read_only_and_offline() -> None:
    source = inspect.getsource(question_coverage)
    for forbidden in ("commit(", "add(", "create_all", "from app.agent", "pydantic_ai"):
        assert forbidden not in source, forbidden


# ------------------------------------------------------------------ 判据 5：分母为 0 与空库
def test_empty_ledger_says_so() -> None:
    session = _fresh_session()
    try:
        assert "无可用台账" in build_report(session)
    finally:
        session.close()


def test_zero_denominator_prints_uncomputable_instead_of_raising() -> None:
    session = _fresh_session()
    try:
        # 一轮 rules 模式/gap 为 None 的回合：付钱 0、分子分母皆 0 —— 不得抛、不得伪造 0%。
        session.add(_classify_span(1, None, "conv-zero"))
        session.commit()
        report = build_report(session)
        assert "不可计算（分母为 0）" in report
    finally:
        session.close()


# ------------------------------------------------------------------ 判据 6：负数显式失败且点名会话
def test_broken_identity_names_the_conversation_and_propagates() -> None:
    session = _fresh_session()
    try:
        consult_gap = {"gate": "consult", "source": "model", "model": {"model_requests": 1}}
        session.add(_classify_span(1, dict(consult_gap), "conv-x"))
        session.add(_classify_span(2, dict(consult_gap), "conv-x"))
        session.add(
            LlmUsageRecord(
                id="u-1", tenant_id="demo", user_id="alice",
                conversation_id="conv-x", source="chat", requests=1,
            )
        )
        session.commit()
        with pytest.raises(ValueError) as raised:
            build_report(session)
        assert "会话 conv-x 的恒等式破裂" in str(raised.value)
    finally:
        session.close()


# ------------------------------------------------------------------ 判据 7：完整报告读数与留痕纪律
def test_report_aggregates_readings_and_keeps_only_counts() -> None:
    session = _fresh_session()
    try:
        formed = {"gate": "question", "source": "model", "model": {"model_requests": 1}}
        consult = {"gate": "consult", "source": "model", "model": {"model_requests": 1}}
        refused = {"gate": "question", "source": "rules"}
        spans = [formed, consult, refused]
        texts = ["运费谁出？", "我申请退一部分款", "帮我看看物流"]
        session.add(Conversation(id="conv-r", tenant_id="demo", user_id="alice"))
        for index, (gap, text) in enumerate(zip(spans, texts, strict=True), start=1):
            session.add(_classify_span(index, dict(gap), "conv-r"))
            session.add(
                LlmUsageRecord(
                    id=f"u-{index}", tenant_id="demo", user_id="alice",
                    conversation_id="conv-r", source="chat", requests=1,
                )
            )
            session.add(
                        ConversationMessage(
                            id=f"m-user-{index}", conversation_id="conv-r",
                            role="user", content=text, payload={},
                            created_at=_BASE_TIME + timedelta(seconds=2 * index - 1),
                        )
            )
            session.add(
                ConversationMessage(
                    id=f"m-assistant-{index}", conversation_id="conv-r",
                    role="assistant", content="答复", payload={"trace_id": f"T{index}"},
                    created_at=_BASE_TIME + timedelta(seconds=2 * index),
                )
            )
        # 另一会话的 task 行不得进读数（边界②）。
        session.add(
            LlmUsageRecord(
                id="u-task", tenant_id="demo", user_id="alice",
                conversation_id="conv-r", source="task", requests=1,
            )
        )
        session.commit()
        report = build_report(session)
        # 恒等式：chat 3 − consult 带 model 1 = 分母 2；分子 = 带 model 的 question gap 1。
        assert "denominator=2" in report
        assert "numerator=1" in report
        assert "paid_no_reading=1" in report
        assert "chat_rows=3" in report
        assert "consult_model_gaps=1" in report
        assert "question_model_gaps=1" in report
        assert "50.0%" in report
        # 诚实低估与不可分辨声明必须在报告里（T5 前置裁定第 2 条）。
        assert "诚实低估" in report
        assert "覆盖率偏低" in report
        assert "不可计算" not in report
        # 留痕纪律：只落计数，客户原文不得出现在报告任何位置。
        for text in texts:
            assert text not in report
    finally:
        session.close()
