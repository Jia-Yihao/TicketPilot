"""R10 T5：问句门覆盖率的纯离线分列报告（口径 §3，入口 ``python evals/question_coverage.py``）。

本模块是 ``CoverageReading`` 与 ``coverage_from_ledger`` 这套算术的**唯一住所**
（T5 前置裁定：不得出现第二套真相）；读数测试
``tests/test_question_coverage_reading.py`` 与本报告共用同一份，方向照仓内先例
``tests/test_trace.py:21``（tests → evals，12 个文件 / 14 处命中；evals → tests 为 0 处）。

只读纪律（口径 §3，仓内**没有**机器守护——``python -m mypy app`` 与
``docs/architecture-guard.yaml`` 都不覆盖 ``evals/``——由
``tests/test_question_coverage_report.py`` 的源码扫描断言机械化）：
本模块不写库、不调模型；import 只有 ``app.models.*`` / ``app.db.session`` /
``app.core.config`` 与标准库、SQLAlchemy 查询构造；gap 只从库里已落盘的
``trace_spans.attributes["model_gap"]`` JSON 读取。

三条硬约束（T5 前置裁定第 4 条）：纯离线、不改 ``evals/intent_metrics.py`` 与
``evals/intent/**``、不加 Makefile 目标。恒等式算出负数**显式失败**并点名会话，
全程没有下限兜底（``max(0, ...)`` 属禁止项）；分母为 0 打印「不可计算（分母为 0）」，
不抛、不伪造 0%。留痕纪律：A count, never the order numbers——输出只有整数计数、
比例、固定标签与四条声明，严禁客户原文、订单号形态串、模型原始输出与提示词片段。
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import build_engine, build_session_factory
from app.models.conversation import ConversationMessage
from app.models.llm_usage import LlmUsageRecord
from app.models.observability import TraceSpan

# --------------------------------------------------------------- 出口列与形态行（封闭值域）
# rules_hit 列名必须如实声明与 rules 模式不可分辨：两者都返回 model_gap=None
# （support_agent.py:1645-1646 的 rules_hit 与 :1647-1648 的 rules 模式闸门），
# 既有键下没有任何读数能把两者分开。规则模式从不付钱、因此从不进分母（口径 §2 分母定义）。
EXIT_RULES_HIT = "rules_hit（或 rules 模式，既有键不可分辨）"
EXIT_QUESTION = "question"
EXIT_CONSULT = "consult"
EXIT_DETAIL_FOLLOWUP = "detail_followup"
EXIT_COLUMNS: tuple[str, ...] = (
    EXIT_RULES_HIT,
    EXIT_QUESTION,
    EXIT_CONSULT,
    EXIT_DETAIL_FOLLOWUP,
)

SHAPE_QUESTION = "问句"
SHAPE_STATEMENT = "陈述"
SHAPE_TEXT_UNAVAILABLE = "文本不可得"
SHAPE_ROWS: tuple[str, ...] = (SHAPE_QUESTION, SHAPE_STATEMENT, SHAPE_TEXT_UNAVAILABLE)


# --------------------------------------------------------------- 读数函数（离线）
@dataclass(frozen=True, slots=True)
class CoverageReading:
    """恒等式一次读数的全部可观察量——只有数，没有原文。"""

    chat_rows: int
    consult_model_gaps: int
    question_model_gaps: int
    denominator: int
    numerator: int
    paid_no_reading: int


def coverage_from_ledger(chat_rows: int, gaps: list[dict[str, Any] | None]) -> CoverageReading:
    """§2 恒等式的算术本体：只读既有 ``gate`` 与 ``model`` 两个键。

    边界 ③：分母为负或分子超过分母，只能是台账被弄脏，必须当场 ValueError，
    绝不允许把负数钳成 0 抹平成一个看着合理的读数。
    """
    consult_model = sum(
        1
        for gap in gaps
        if gap is not None and gap.get("gate") == "consult" and "model" in gap
    )
    question_model = sum(
        1
        for gap in gaps
        if gap is not None and gap.get("gate") == "question" and "model" in gap
    )
    denominator = chat_rows - consult_model
    if denominator < 0:
        raise ValueError(
            f"恒等式破裂：consult 带 model 键数({consult_model}) 大于 chat 行数({chat_rows})"
        )
    paid_no_reading = denominator - question_model
    if paid_no_reading < 0:
        raise ValueError(
            f"恒等式破裂：分子({question_model}) 大于分母({denominator})"
        )
    return CoverageReading(
        chat_rows=chat_rows,
        consult_model_gaps=consult_model,
        question_model_gaps=question_model,
        denominator=denominator,
        numerator=question_model,
        paid_no_reading=paid_no_reading,
    )


# --------------------------------------------------------------- 分类纯函数（零模型、确定性）
def classify_shape(text: str | None) -> str:
    """「问句 / 陈述」形态判定：输入只能是配对到的请求文本本身（口径 §3）。

    代价声明（随报告输出）：对话侧 user 行存的是本轮请求文本，同一客户连发多条时
    配对取的是**最后一条**（覆盖语义），所以这一分列是**措辞形态的近似**，不是语义
    标注；本表只落计数，不落原文。
    """
    if text is None:
        return SHAPE_TEXT_UNAVAILABLE
    if "？" in text or "?" in text:
        return SHAPE_QUESTION
    return SHAPE_STATEMENT


def exit_of_gap(gap: dict[str, Any] | None) -> str:
    """gap → 出口列。值域封闭，未登记值必须显式失败（先例：未知 kind 抛错不静默回落）。"""
    if gap is None:
        return EXIT_RULES_HIT
    gate = gap.get("gate")
    if gate == "question":
        return EXIT_QUESTION
    if gate == "consult":
        return EXIT_CONSULT
    if gate == "detail_followup":
        # detail_followup（support_agent.py:879-889 形态，:890 显式 model_trace=None）
        # 分子分母两侧都不计，但它是既有键下真实存在的第四种出口，列必须给全。
        return EXIT_DETAIL_FOLLOWUP
    raise ValueError(f"未登记的 gate 值：{gate!r}")


def pair_request_texts(messages: Iterable[ConversationMessage]) -> dict[str, str]:
    """按 ``evals/replay.py:172-186`` 的既有配对形状，把 user 文本配到 assistant 行的 trace_id。

    明确**不复用** ``evals/replay.py:189-192`` 的按序位置兜底：那会把历史轮次的文本
    配到错误的 trace 上，形态判定就会假。缺 ``trace_id`` 的轮次不进映射——在报告里
    一律是「文本不可得」。悬挂的 user 行（接管态静默回合 ``SupportAgent._silent``
    只写 user 消息、不写 assistant 行）由覆盖语义天然容忍，不加特判。
    """
    texts: dict[str, str] = {}
    pending_user: ConversationMessage | None = None
    for message in messages:
        if message.role == "user":
            pending_user = message
        elif message.role == "assistant" and pending_user is not None:
            trace_id = (message.payload or {}).get("trace_id")
            if trace_id is not None:
                if trace_id in texts:
                    raise ValueError(f"配对错误：trace {trace_id} 被配对两次")
                texts[trace_id] = pending_user.content
            pending_user = None
    return texts


# --------------------------------------------------------------- 台账读取（只 SELECT，无副作用）
def _conversation_ids(session: Session) -> list[str]:
    """待读会话 = 有 classify span 的 ∪ 有 chat 用量行的（都只到会话粒度，边界 ①）。"""
    span_ids = session.scalars(
        select(TraceSpan.conversation_id)
        .where(
            TraceSpan.kind == "understand",
            TraceSpan.name == "support.classify",
            TraceSpan.conversation_id.is_not(None),
        )
        .distinct()
    ).all()
    usage_ids = session.scalars(
        select(LlmUsageRecord.conversation_id)
        .where(
            LlmUsageRecord.source == "chat",
            LlmUsageRecord.conversation_id.is_not(None),
        )
        .distinct()
    ).all()
    return sorted({str(value) for value in (*span_ids, *usage_ids)})


def _chat_rows(session: Session, conversation_id: str) -> int:
    """只取字面量 ``source="chat"``（边界 ②：task/analytics 行必须排除，不做反选）。"""
    return int(
        session.scalar(
            select(func.count())
            .select_from(LlmUsageRecord)
            .where(
                LlmUsageRecord.source == "chat",
                LlmUsageRecord.conversation_id == conversation_id,
            )
        )
        or 0
    )


def _classify_spans(
    session: Session, conversation_id: str
) -> list[tuple[str, dict[str, Any] | None]]:
    """按 (started_at, id) 读本会话全部 classify span 的 (trace_id, model_gap)。"""
    rows = session.execute(
        select(TraceSpan.trace_id, TraceSpan.attributes)
        .where(
            TraceSpan.kind == "understand",
            TraceSpan.name == "support.classify",
            TraceSpan.conversation_id == conversation_id,
        )
        .order_by(TraceSpan.started_at, TraceSpan.id)
    ).all()
    return [(trace_id, dict(attributes or {}).get("model_gap")) for trace_id, attributes in rows]


def _conversation_messages(session: Session, conversation_id: str) -> list[ConversationMessage]:
    return list(
        session.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at, ConversationMessage.id)
        ).all()
    )


def _reading_for(
    session: Session, conversation_id: str, spans: list[tuple[str, dict[str, Any] | None]]
) -> CoverageReading:
    """边界 ③：逐会话跑恒等式；破裂必须点名是哪个会话，失败原样上抛，不做任何抹平。"""
    try:
        return coverage_from_ledger(
            _chat_rows(session, conversation_id), [gap for _, gap in spans]
        )
    except ValueError as err:
        raise ValueError(f"会话 {conversation_id} 的恒等式破裂：{err}") from err


# --------------------------------------------------------------- 聚合与输出（只落计数）
def build_report(session: Session) -> str:
    """按会话逐个跑恒等式并聚合，返回完整报告文本（纯读，不产生任何写操作）。"""
    conversation_ids = _conversation_ids(session)
    if not conversation_ids:
        return (
            "无可用台账：库里既没有 understand/support.classify span，"
            "也没有 source=chat 的用量行。"
        )

    counts: dict[str, dict[str, int]] = {row: dict.fromkeys(EXIT_COLUMNS, 0) for row in SHAPE_ROWS}
    totals = CoverageReading(0, 0, 0, 0, 0, 0)
    for conversation_id in conversation_ids:
        spans = _classify_spans(session, conversation_id)
        reading = _reading_for(session, conversation_id, spans)
        totals = CoverageReading(
            chat_rows=totals.chat_rows + reading.chat_rows,
            consult_model_gaps=totals.consult_model_gaps + reading.consult_model_gaps,
            question_model_gaps=totals.question_model_gaps + reading.question_model_gaps,
            denominator=totals.denominator + reading.denominator,
            numerator=totals.numerator + reading.numerator,
            paid_no_reading=totals.paid_no_reading + reading.paid_no_reading,
        )
        texts = pair_request_texts(_conversation_messages(session, conversation_id))
        for trace_id, gap in spans:
            counts[classify_shape(texts.get(trace_id))][exit_of_gap(gap)] += 1

    turn_total = sum(sum(row.values()) for row in counts.values())
    lines: list[str] = ["问句门覆盖率报告（纯离线读数，零模型调用、零写库）", ""]

    lines.append(f"分列 × 出口（回合数 {turn_total}；只落计数，不落客户原文）")
    lines.append("\t".join(["形态", *EXIT_COLUMNS, "行合计"]))
    for row in SHAPE_ROWS:
        cells = []
        for column in EXIT_COLUMNS:
            value = counts[row][column]
            share = f"{100 * value / turn_total:.1f}%" if turn_total else "-"
            cells.append(f"{value} ({share})")
        row_total = sum(counts[row].values())
        lines.append("\t".join([row, *cells, str(row_total)]))
    column_totals = [
        str(sum(counts[row][column] for row in SHAPE_ROWS)) for column in EXIT_COLUMNS
    ]
    lines.append("\t".join(["列合计", *column_totals, str(turn_total)]))
    lines.append("")

    lines.append("覆盖率读数（按会话逐个跑恒等式后求和；复用 reused 不做任何调整）")
    lines.append(
        f"chat_rows={totals.chat_rows} consult_model_gaps={totals.consult_model_gaps} "
        f"question_model_gaps={totals.question_model_gaps} denominator={totals.denominator} "
        f"numerator={totals.numerator} paid_no_reading={totals.paid_no_reading}"
    )
    if totals.denominator == 0:
        lines.append("coverage = 不可计算（分母为 0）")
    else:
        rate = 100 * totals.numerator / totals.denominator
        lines.append(
            f"coverage = numerator/denominator = {totals.numerator}/{totals.denominator} "
            f"= {rate:.1f}%"
        )
    lines.append("")

    lines.extend(
        [
            "诚实低估声明：support_agent.py:1691-1696 出口（探针确曾产出合法读数、却因标签表",
            "凑不出完整问句而未成反问）返回的 gap 与 :1681「已付费无读数」出口逐字相同",
            '（source 仍是 "rules"、无 model 键），既有键下不可区分；此类轮次按「无读数」计，',
            "覆盖率偏低。按 R-2 不解锁新键，不得加键补齐。",
            "",
            "rules_hit 声明：gap 为 None 的轮次里，规则模式与 rules_hit 出口在既有键下不可分辨",
            "（两处都返回 model_gap=None）；规则模式从不付钱、从不进分母。",
            "",
            "detail_followup 声明：gate=detail_followup 的轮次（:890 显式 model_trace=None）",
            "分子分母两侧都不计，只作为第四出口列呈现。",
            "",
            "文本不可得声明：请求文本按 (trace_id ← assistant 行 payload) 配对；缺 trace_id、",
            "或消息行已被会话回合上限裁剪（conversations.py:13 MAX_STORED_TURNS=40，回合写回",
            "时裁剪旧消息，trace_spans 仍在而消息行已删）的轮次，一律计为「文本不可得」，不猜、",
            "不做位置兜底；出口列按 gap 统计、形态行按文本可得性统计，两套各自完整。",
            "",
            "形态判定声明：user 行存的是本轮请求文本，连发多条时配对取最后一条（覆盖语义），",
            "「问句/陈述」分列是措辞形态的近似，不是语义标注。",
            "",
            "复用声明：对话侧 support_agent.py:911 落 chat 行没有 reused 闸（任务侧 runtime.py:433",
            "明确跳过），这是口径已登记的既有事实（处置归 R12）；本表不为此新增键、不做减法——",
            "若因此出现负数，走上文的显式失败路径。",
        ]
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """唯一命令行参数 ``--database-url``（缺省用配置默认库）；任何异常非零退出，失败可观察。"""
    parser = argparse.ArgumentParser(
        description="Print the offline question-gate coverage report (counts only).",
    )
    parser.add_argument(
        "--database-url",
        help="Read the ledger from this database instead of the configured default.",
    )
    args = parser.parse_args(argv)
    engine = build_engine(args.database_url or get_settings().database_url)
    try:
        with build_session_factory(engine)() as session:
            print(build_report(session))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
