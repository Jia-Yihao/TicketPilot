"""R10 T4：把口径 §2「T4 前置裁定」的恒等式钉成可执行断言。

口径 ``.qoder/rules/question-gate-coverage-and-channel-intent.md`` §2「T4 前置裁定」：

    同一会话的 ``chat`` 行数 = 成反问数 + 付费未成句数 + consult 带 ``model`` 键数
    ⟹ 分母 = chat 行数 − consult 带 ``model`` 键数
    ⟹ 分子 = 带 ``model`` 键（``source`` 已非 ``"rules"``）的 ``question`` gap 数
    ⟹ 分母 − 分子 = 「已付费无读数」两类轮次（support_agent.py:1681 拒答、:1696 缺标签）

分类只依据既有 ``gate``/``model`` 键，**不新增键**（R-2 不解锁）。读数算术住在
``evals/question_coverage.py``（T5 算术搬家：本文件与 T5 报告共用同一套算术，
仓内不得有第二份），不进生产路径（``app/**`` 零改动）。

边界三条（§2 前置裁定）：

① 关联粒度只有会话级——``app/models/llm_usage.py`` 有 ``source``/``conversation_id``/
   ``task_id``，**没有** ``trace_id`` 列；恒等式只能在 ``conversation_id`` 上成立。
② ``source="task"``（app/services/runtime.py:438/:442）与 ``source="analytics"``
   （app/agent/actions.py:657）的成本行必须被排除，读数只认 ``source="chat"``。
③ 分母 − 分子为负必须炸（ValueError），不得 ``max(0, ...)`` 抹平。

用例 ↔ 判据对照：

* test_identity_holds_on_a_real_mixed_batch —— 判据 1（真实链路混批）+ 边界 ①
* test_rules_mode_never_enters_the_denominator —— 判据 2
* test_paid_round_with_missing_labels_counts_into_the_denominator_only —— 判据 3
  （已知低估：本轮与 :1681 拒答在既有键下不可分辨，按 R-2 **不新增键**修正）
* test_a_real_detail_followup_round_counts_on_neither_side —— 判据 4（真实链路：
  app_factory + TestClient 两句消息，gap 与 chat 行全部从库读回）
* test_detail_followup_gap_counts_on_neither_side —— 判据 4（函数级补充：
  守护读数纯函数在混批中不偏置两项，与真实链路用例分工见该用例）
* test_reading_counts_chat_source_only —— 判据 5 = 边界 ②
* test_negative_identity_fails_loudly —— 判据 6 = 边界 ③
* test_reused_consult_round_never_inflates_the_numerator —— 判据 7（加分项）

模型不出现：沿用 tests/test_llm_gap 的 FixedBrain/ExplodingAgent 替身；判据 4 的
真实链路用例沿用 tests/test_analytics_followup 的 StubNarrator 顶掉叙述模型。断言只数
数、不看客户原文，也不把模型原始输出写进任何断言。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent.counter_question import CounterQuestionSpec
from app.agent.pydantic_agent import IntentAgent, IntentBrain
from app.agent.support_agent import _QUESTION_GAP
from app.models.llm_usage import LlmUsageRecord
from app.models.observability import TraceSpan
from app.services.llm_usage import LlmUsageService
from evals.question_coverage import CoverageReading, coverage_from_ledger
from tests.conftest import ALICE
from tests.test_analytics_followup import StubNarrator
from tests.test_llm_gap import ExplodingAgent, FixedBrain, _app

# 问句形态、规则读成 UNKNOWN（tests/test_counter_question_turn 同款演示常量）。
QUESTION_TEXT = "运费谁出？"
# 非问句形态、规则读 UNKNOWN —— consult 档入口。
CONSULT_GAP_TEXT = "我申请退一部分款"


def conversation_chat_rows(app: FastAPI, conversation_id: str) -> int:
    """只数 ``source="chat"`` 且挂在本会话上的成本行（边界 ① 的粒度 + 边界 ② 的过滤）。"""
    with app.state.session_factory() as session:
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


def conversation_gaps(app: FastAPI, conversation_id: str) -> list[dict[str, Any] | None]:
    """从 ``trace_spans`` 表读本会话全部 ``understand/support.classify`` 的 model_gap。"""
    with app.state.session_factory() as session:
        rows = list(
            session.execute(
                select(TraceSpan.attributes)
                .where(
                    TraceSpan.kind == "understand",
                    TraceSpan.name == "support.classify",
                    TraceSpan.conversation_id == conversation_id,
                )
                .order_by(TraceSpan.started_at, TraceSpan.id)
            )
        )
    return [dict(row[0] or {}).get("model_gap") for row in rows]


def read_conversation_coverage(app: FastAPI, conversation_id: str) -> CoverageReading:
    """§2 恒等式的会话级读数：全部输入都从库里来，不读任何进程内状态。"""
    return coverage_from_ledger(
        conversation_chat_rows(app, conversation_id),
        conversation_gaps(app, conversation_id),
    )


def _db_gap_for_trace(app: FastAPI, trace_id: str) -> dict[str, Any] | None:
    """按 trace_id 取本轮 classify span 的 model_gap（只用于逐轮核对形态）。"""
    with app.state.session_factory() as session:
        rows = list(
            session.execute(
                select(TraceSpan.attributes).where(
                    TraceSpan.kind == "understand",
                    TraceSpan.name == "support.classify",
                    TraceSpan.trace_id == trace_id,
                )
            )
        )
    assert len(rows) == 1, rows
    return dict(rows[0][0] or {}).get("model_gap")


# ------------------------------------------------------------------ 测试侧替身形态
def _formed_question_gap() -> dict[str, Any]:
    """support_agent.py:1697-1702 形成反问的 gap：source 已非 rules，带 model 键。"""
    gap = dict(_QUESTION_GAP)
    gap["source"] = "pydantic_ai"
    gap["model"] = {"model_requests": 1, "model_reused": False}
    return gap


def _consult_gap(*, reused: bool = False) -> dict[str, Any]:
    """support_agent.py:1719-1735 consult 且 model_trace 非 None 的 gap。"""
    return {
        "source": "pydantic_ai",
        "intent": "UNKNOWN",
        "adopted": False,
        "gate": "consult",
        "redirect": False,
        "redirect_rejected": False,
        "redirect_kind": "none",
        "model": {"model_requests": 1, "model_reused": reused},
    }


def _detail_followup_gap() -> dict[str, Any]:
    """support_agent.py:879-892 逐字形态（model_trace=None 于 :890，故无 chat 行）。"""
    return {
        "source": "rules",
        "intent": "SELF_ANALYTICS",
        "adopted": False,
        "gate": "detail_followup",
        "redirect": False,
        "redirect_rejected": False,
        "redirect_kind": "none",
    }


# ------------------------------------------------------------------------ 公共动作
def _send(client: TestClient, message: str, conversation_id: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": message, "conversation_id": conversation_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _seed_other_source_rows(
    app: FastAPI, conversation_id: str, sources: tuple[str, ...]
) -> None:
    """往同一会话按生产写法（LlmUsageService.record）塞指定来源的成本行。"""
    with app.state.session_factory() as session:
        service = LlmUsageService(session)
        for source in sources:
            service.record(
                tenant_id="demo",
                user_id="alice",
                conversation_id=conversation_id,
                source=source,
                planning_source="pydantic_ai",
                requests=1,
            )
        session.commit()


def _rows_by_source(app: FastAPI, conversation_id: str, source: str) -> int:
    with app.state.session_factory() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(LlmUsageRecord)
                .where(
                    LlmUsageRecord.conversation_id == conversation_id,
                    LlmUsageRecord.source == source,
                )
            )
            or 0
        )


def _all_chat_rows(app: FastAPI) -> int:
    with app.state.session_factory() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(LlmUsageRecord)
                .where(LlmUsageRecord.source == "chat")
            )
            or 0
        )


# -------------------------------------------------------------------------- 判据 1
def test_identity_holds_on_a_real_mixed_batch(tmp_path: Path) -> None:
    """真实链路混批：同一会话里成反问 / 付费未成句 / consult 带 model 键各≥1，
    恒等式三个量全部从库读（llm_usage + trace_spans），逐条对上 §2。"""
    brain = FixedBrain("UNKNOWN")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    # 轮 1：探针付了钱但 model_unknown 拒答（:1677-1681）——付费未成句之一。
    # 拒答不消耗 F-1 上限位，所以后面还能成反问。
    refused = _send(client, QUESTION_TEXT, "cover-mixed")
    conv = refused["conversation_id"]
    refused_gap = _db_gap_for_trace(app, refused["trace_id"])
    assert refused_gap == dict(_QUESTION_GAP)  # 字面量逐字，source 仍 "rules"
    assert "model" not in refused_gap

    # 轮 2：合法读数凑成反问（:1697-1715）——分子唯一来源。
    brain.intent = "POLICY_QA"
    formed = _send(client, QUESTION_TEXT, conv)
    assert formed["clarification"] is not None
    formed_gap = _db_gap_for_trace(app, formed["trace_id"])
    assert formed_gap is not None
    assert formed_gap["gate"] == "question" and "model" in formed_gap
    assert formed_gap["source"] == "pydantic_ai"

    # 轮 3：consult 档且 model_trace 非 None（:1716-1735）——分母里要扣的那一项。
    brain.intent = "UNKNOWN"
    consult = _send(client, CONSULT_GAP_TEXT, conv)
    consult_gap = _db_gap_for_trace(app, consult["trace_id"])
    assert consult_gap is not None
    assert consult_gap["gate"] == "consult" and "model" in consult_gap

    # 边界 ②：同会话混入 task / analytics 成本行，读数必须无视它们，
    # 恒等式在混批上逐项闭合（不掺假 chat 行——假 chat 行的过滤在判据 5 单测）。
    _seed_other_source_rows(app, conv, ("task", "analytics"))
    assert _rows_by_source(app, conv, "task") == 1
    assert _rows_by_source(app, conv, "analytics") == 1

    # 边界 ①：另一会话也付了一行 chat；关联只有会话级。
    brain.intent = "UNKNOWN"
    other = _send(client, QUESTION_TEXT, "cover-other")
    other_conv = other["conversation_id"]

    reading = read_conversation_coverage(app, conv)
    # 本会话真实付掉的 chat 行：轮 1/2/3 各一；task/analytics 两行不进读数。
    assert reading.chat_rows == 3
    assert len(conversation_gaps(app, conv)) == 3
    assert reading.consult_model_gaps == 1
    # 恒等式逐项：分母 = chat 行数 − consult 带 model 键数
    assert reading.denominator == reading.chat_rows - reading.consult_model_gaps == 2
    # 分子 = 带 model 键的 question gap 数
    assert reading.numerator == 1
    # 分母 − 分子 = 已付费无读数：本轮恰好是轮 1（:1681 拒答）
    assert reading.paid_no_reading == reading.denominator - reading.numerator == 1

    other_reading = read_conversation_coverage(app, other_conv)
    assert other_reading.chat_rows == 1
    assert other_reading.denominator == 1 and other_reading.numerator == 0
    # 跨会话合起来仍然闭合：chat 总行数只等于各会话读数之和（会话级粒度）
    assert _all_chat_rows(app) == reading.chat_rows + other_reading.chat_rows
    # 边界 ① 的表结构证据：台账根本没有 trace_id 可 join。
    assert "trace_id" not in {column.name for column in LlmUsageRecord.__table__.columns}


# -------------------------------------------------------------------------- 判据 2
def test_rules_mode_never_enters_the_denominator(tmp_path: Path) -> None:
    """AGENT_MODE=rules：同形消息进来，chat 行数 +0、gap 为 None、分母恒 0。"""
    brain = IntentBrain(mode="rules", agent=cast("IntentAgent", ExplodingAgent()))
    app = _app(tmp_path, brain, agent_mode="rules")
    client = TestClient(app)

    first = _send(client, QUESTION_TEXT, "cover-rules")
    conv = first["conversation_id"]
    _send(client, CONSULT_GAP_TEXT, conv)

    assert conversation_chat_rows(app, conv) == 0
    assert conversation_gaps(app, conv) == [None, None]
    reading = read_conversation_coverage(app, conv)
    assert reading.denominator == 0
    assert reading.numerator == 0
    assert reading.paid_no_reading == 0


# -------------------------------------------------------------------------- 判据 3
def test_paid_round_with_missing_labels_counts_into_the_denominator_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§2 登记的**已知低估**（support_agent.py:1691-1696）：探针给出合法读数、
    但标签表凑不出问句 —— 本轮付了钱、gap 与今天逐字相等（source 仍 "rules"、
    无 model 键），于是只进分母、被算进「付费未成句」。它与 :1681 拒答在既有键
    下不可分辨；按 R-2 不解锁，**不新增键**去修，本用例只把这个低估钉成事实。

    标签缺失是测试内临时构造（monkeypatch 只在测试里替换 get_counter_question），
    生产文件零改动。
    """
    stub = CounterQuestionSpec(
        version="labelgap-test",
        prompt_template="{options}",
        labels={},  # 读数 POLICY_QA 在标签表里查不到 → :1691 draft 为 None
        max_options=6,
    )
    monkeypatch.setattr("app.agent.support_agent.get_counter_question", lambda: stub)

    brain = FixedBrain("POLICY_QA")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, QUESTION_TEXT, "cover-labelgap")
    conv = body["conversation_id"]
    assert body["clarification"] is None  # 没成反问

    gap = _db_gap_for_trace(app, body["trace_id"])
    assert gap == dict(_QUESTION_GAP)  # 与拒答轮逐字同形 —— 低估的根源
    assert gap is not None
    assert gap["source"] == "rules"
    assert "model" not in gap

    reading = read_conversation_coverage(app, conv)
    assert reading.chat_rows == 1  # 探针付了钱，成本行照落
    assert reading.denominator == 1
    assert reading.numerator == 0
    assert reading.paid_no_reading == 1


# -------------------------------------------------------------------------- 判据 4
def test_a_real_detail_followup_round_counts_on_neither_side(
    app_factory,  # type: ignore[no-untyped-def]
) -> None:
    """判据 4 的真实链路版：沿用 tests/test_analytics_followup.py:68-93 的两句
    消息形状 —— 先「我买了多少订单」开快照，再在**同一会话**发「哪几笔」——
    第二回合真的走进 support_agent.py:873-892 的 detail_followup 分支；gap、
    chat 行与三项读数全部从库里读回，不是测试自写字典。

    叙述用 StubNarrator 替身（挂在 application.state.support_agent.narrator，
    同 tests/test_analytics_followup.py:71-73 的做法），生产零改动。AGENT_MODE
    取 app_factory 默认的 rules：两轮分别走 rules 直读（首轮 gap 为 None）与
    detail_followup（:890 ``model_trace = None``，不产 chat 行）——以下读数
    均为该分支形态的实测：gaps == [None, <detail_followup dict>]、
    chat_rows == 0、三项读数全 0。
    """
    application: FastAPI = app_factory()
    application.state.support_agent.narrator = StubNarrator()
    with TestClient(application) as client:
        first = _send(client, "我买了多少订单", "cover-realfollowup")
        conv = first["conversation_id"]
        assert first["slots"]["last_analytics_query_id"] is not None
        before = read_conversation_coverage(application, conv)
        follow = _send(client, "哪几笔", conv)

    # 第二句确实经 detail_followup 分支取回了同一张快照（不是别的意图顺带命中）。
    assert follow["context_cards"][0]["kind"] == "analytics_detail"
    assert follow["slots"]["last_analytics_query_id"] == first["slots"][
        "last_analytics_query_id"
    ]

    gaps = conversation_gaps(application, conv)
    assert len(gaps) == 2
    assert gaps[0] is None  # 首轮 rules 直读，不产 gap
    follow_gap = gaps[1]
    assert follow_gap is not None
    # 逐字对照 support_agent.py:879-889 的生产形态。
    assert follow_gap["gate"] == "detail_followup"
    assert follow_gap["source"] == "rules"
    assert "model" not in follow_gap
    # 与本文件按 :879-889 手写的形态逐字一致 —— 生产形态与纯函数替身未分叉。
    assert follow_gap == _detail_followup_gap()

    # 两轮都未付模型钱：chat 行 0；follow-up 轮前后读数逐字段相同。
    assert conversation_chat_rows(application, conv) == 0
    after = read_conversation_coverage(application, conv)
    assert after == before
    assert after.denominator == 0
    assert after.numerator == 0
    assert after.paid_no_reading == 0

    # 纯函数对照：同一批三轮混批多掺一条从库读回的真实 detail_followup gap，
    # 全部读数项不变。
    mixed = [dict(_QUESTION_GAP), _formed_question_gap(), _consult_gap()]
    baseline = coverage_from_ledger(3, mixed)
    with_followup = coverage_from_ledger(3, mixed + [follow_gap])
    assert with_followup == baseline


def test_detail_followup_gap_counts_on_neither_side() -> None:
    """判据 4 的函数级补充。真实链路由
    test_a_real_detail_followup_round_counts_on_neither_side 覆盖（app_factory +
    TestClient 两句消息产出真实的 detail_followup 轮，gap 从库读回）；本条与之
    分工互补：那条守护「生产形态与读数在真链路上成立」，本条守护**读数纯函数**
    对 ``gate="detail_followup"``（support_agent.py:879-889 形态，:890
    ``model_trace=None`` 故本轮不产 chat 行）的分类——混进一批 consult/question
    gap 时两个计数项都不沾，恒等式不因它偏置。
    """
    reading = coverage_from_ledger(0, [_detail_followup_gap()])
    assert reading.denominator == 0
    assert reading.numerator == 0
    assert reading.paid_no_reading == 0

    # 混进一批也不改变两项：chat 行仍恰好对应拒答/成句/consult 三轮。
    mixed = [
        _detail_followup_gap(),
        dict(_QUESTION_GAP),  # 付费未成句（拒答形态）
        _formed_question_gap(),
        _consult_gap(),
    ]
    batch = coverage_from_ledger(3, mixed)
    assert batch.consult_model_gaps == 1
    assert batch.denominator == 2
    assert batch.numerator == 1
    assert batch.paid_no_reading == 1


# -------------------------------------------------------------------------- 判据 5
def test_reading_counts_chat_source_only(tmp_path: Path) -> None:
    """边界 ②：``source="task"``（runtime.py:438/:442）与 ``source="analytics"``
    （actions.py:657）的行即便挂在同一会话上，也不得进读数 —— 只认 chat。"""
    brain = FixedBrain("UNKNOWN")
    app = _app(tmp_path, brain)  # 一个真实对话轮都不跑，隔离来源过滤本身
    conv = "cover-source-only"
    _seed_other_source_rows(app, conv, ("task", "analytics", "chat"))

    assert _rows_by_source(app, conv, "task") == 1
    assert _rows_by_source(app, conv, "analytics") == 1
    assert conversation_chat_rows(app, conv) == 1  # 三行里只数出 chat 那一行
    assert _rows_by_source(app, conv, "chat") == 1


# -------------------------------------------------------------------------- 判据 6
def test_negative_identity_fails_loudly() -> None:
    """边界 ③：负数必须炸，不得 max(0, ...) 抹平。"""
    # consult 带 model 键数超过 chat 行数 → 分母为负。
    with pytest.raises(ValueError):
        coverage_from_ledger(1, [_consult_gap(), _consult_gap()])
    # 分子超过分母 → 差为负。
    with pytest.raises(ValueError):
        coverage_from_ledger(
            2,
            [_formed_question_gap(), _formed_question_gap(), _formed_question_gap()],
        )
    # 读数实现里不允许出现任何平滑：源码扫描自证没有 max(0, ...) 类遮蔽。
    for fn in (coverage_from_ledger, conversation_chat_rows, conversation_gaps):
        assert "max(" not in inspect.getsource(fn), fn.__name__


# -------------------------------------------------------------------------- 判据 7
def test_reused_consult_round_never_inflates_the_numerator() -> None:
    """自反（pydantic_agent.py:600 ``ModelTrace.from_dict(decision.trace,
    reused=True)``，``reused`` 字段 :171，键 ``model_reused`` :246）：复用只可能
    出现在 consult 档 —— 成反问 gap 的 model 键来自探针现调 trace。复用轮
    chat 行 +1、consult 带 model 键 +1，恰好对消，分子恒不沾。

    读数只依据既有 gate/model 键，**从不读** ``model_reused``（源码自证）。
    reused 在 chat 侧是否重复计费的一般项（对话侧 `LlmUsageService.record` 的
    ``source="chat"`` 落账点无复用闸，本轮时点读数在 ``support_agent.py:959``，
    ``record`` 调用在 `:955`；对照任务侧 ``runtime.py:433`` 的 ``if not
    model_trace.reused:``）属 R12，本任务不裁定。行号为时点读数，可能漂移。
    """
    gaps = [
        _consult_gap(reused=True),
        dict(_QUESTION_GAP),
        _formed_question_gap(),
    ]
    reading = coverage_from_ledger(3, gaps)
    assert reading.consult_model_gaps == 1
    assert reading.denominator == 2
    assert reading.numerator == 1
    assert reading.paid_no_reading == 1
    assert "reused" not in inspect.getsource(coverage_from_ledger)
