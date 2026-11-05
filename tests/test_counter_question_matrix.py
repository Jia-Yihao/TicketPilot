"""F1-T6 完整矩阵：把 F-1 验收口径 §12 的三处矩阵缺口钉成用例。

口径 `app/agent/counter_question.yaml`。T1–T5 各步已钉住
本步改动面必须成立的最小事实（收集数与归属见各文件 docstring），本文件
只补矩阵里此前没有实物钉住的三格：

- **§12.1**（探针转人工句）：`scripts/llm_gap_probe.py:100-102` 的三句，
  在 ``AGENT_MODE=pydantic_ai`` 下逐句核对期望——前两句（词表外）换来一次反问
  （回复为问句、``suggestions`` 含选项、恰一条 ``counter_question.asked``）；
  第三句含 ``真人`` cue，走既有 ``HUMAN_HANDOFF`` 通道、零模型调用、零反问
  （§6.2「客户说转人工走既有 cue」的同源事实）。「至少一种变成反问」由
  前两句满足。探针 11 句的**实跑读数属用户侧**（口径 §13.5），本用例钉的是
  期望行为，不是实测探针。
- **§12.8**（探针拒绝三分支）：``NONE`` / 越界 / 模型失败三个拒答原因在
  **回合消费面**钉住同等处理——不反问、回复逐字落回今天的通用菜单、
  ``model_gap`` 键集与落地前逐字相等、上限位不被消耗、审计零行、成本行
  照落（付了钱就必须可观察，§3.2/§13.3）。消费面只判 ``probe.asks``
  （``app/agent/support_agent.py`` 的单一道闸），因此本格钉的是「闸不按拒答
  原因分叉」；三分支各自的**生产者**判定已由 ``tests/test_question_probe.py``
  用真实 ``IntentBrain`` 逐条钉住 ``asks`` 为假。
- **§12.10**（§6.3 三场景）：反问之后的答非所问 / 拒绝 / 沉默——不死锁、
  不凭空建任务、转人工随时可达；共享升级预算（§5.2）：反问轮
  ``clarification_count`` +1，之后不新设计数器。

模型不出现：问句门与 consult 出口沿用既有替身——``FixedBrain``
（``tests/test_llm_gap``）、``RecordingAgent`` + 真实 ``IntentBrain``
（``tests/test_intent_candidate_model``），本文件不发明新假件，只在
``FixedBrain`` 的探针支上替换 ``QuestionProbe`` 的拒答原因。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent.pydantic_agent import QuestionProbe
from app.models.handoff import HandoffEvent
from tests.test_consult_counter_question import (
    _HANDOFF_TAIL,
    COUNTER_REASON,
    _consult_brain,
)
from tests.test_counter_question_audit import _audit_rows
from tests.test_counter_question_turn import (
    QUESTION_TEXT,
    TODAYS_QUESTION_GAP,
    UNKNOWN_MENU_REPLY,
    _chat_rows,
    _send,
    _understand,
)
from tests.test_llm_gap import FixedBrain, _app, _tasks

# 探针 `scripts/llm_gap_probe.py:100-102` 的三句转人工话术，逐字副本。
# 前两句在词表外（规则读 UNKNOWN），第三句含 HUMAN_HANDOFF 的 `真人` cue。
GAP_HANDOFF_SENTENCES = ("别跟我绕了，叫个活人来", "我要找你们负责人，机器人在糊弄我")
CUED_HANDOFF_SENTENCE = "这事儿我忍了三次了，给我找个真人"

# 反问之后的「答非所问」与「拒绝」各一句（均非问句形态、规则读 UNKNOWN）。
OFF_TARGET_ANSWER = "我先看看再说"
REFUSAL_ANSWER = "都不是"


class _RefusingProbeBrain(FixedBrain):
    """把 FixedBrain 探针支的拒答原因换成指定值，其余照旧。

    底座取 ``intent="UNKNOWN"`` 的默认探针（候选集与成本行都真实产出），
    只替换 ``reason``——§12.8 要钉的是**消费面**对三种拒答的同等处理。
    """

    def __init__(self, reason: str) -> None:
        super().__init__("UNKNOWN")
        self.refusal_reason = reason

    def probe_question(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        *,
        order_state: str | None = None,
    ) -> QuestionProbe:
        base = super().probe_question(request, context, order_state=order_state)
        return QuestionProbe(
            None, base.candidate_set, base.trace, base.source, self.refusal_reason
        )


# ---------------------------------------------------------------- §12.1 探针句


@pytest.mark.parametrize("sentence", GAP_HANDOFF_SENTENCES)
def test_a_handoff_gap_sentence_buys_exactly_one_counter_question(
    tmp_path: Path, sentence: str
) -> None:
    """§12.1：词表外的转人工说法换来一次反问——问句、选项、恰一条审计。"""
    brain, agent = _consult_brain("TICKET_CREATE")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(
        client, sentence, f"t6-probe-{GAP_HANDOFF_SENTENCES.index(sentence)}"
    )

    assert body["intent"] == "UNKNOWN"  # 反问不改路由（§2.1）
    clarification = body["clarification"]
    assert clarification is not None
    assert clarification["reason"] == COUNTER_REASON
    assert body["suggestions"]  # 选项来自同一轮的候选集，不是凭空造
    assert len(agent.calls) == 1  # consult 出口一次 plan，没有第二次候选调用
    reply = body["reply"]
    assert reply.startswith(clarification["prompt"])  # 主干是 YAML 问句
    assert reply.endswith(_HANDOFF_TAIL)  # 尾句复用，客户随时可转人工
    rows = _audit_rows(app)
    assert len(rows) == 1
    detail = rows[0].detail
    assert detail["gate"] == "consult"
    assert detail["reading_intent"] == "TICKET_CREATE"
    assert detail["option_count"] == len(body["suggestions"])
    assert body["slots"]["counter_question_asked"] is True


def test_a_handoff_cue_sentence_still_takes_the_existing_path(tmp_path: Path) -> None:
    """§12.1 第三句 + §6.2：含 `真人` cue 的话术走既有 HUMAN_HANDOFF，零反问。"""
    brain, agent = _consult_brain("TICKET_CREATE")  # 替身读数凑得出反问也不问：规则已命中
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, CUED_HANDOFF_SENTENCE, "t6-probe-cued")

    assert body["intent"] == "HUMAN_HANDOFF"
    assert body["handoff"]["signal_id"] == "user_request"
    assert body["clarification"] is None
    assert len(agent.calls) == 0  # rules_hit：模型一次都没被叫到
    assert _audit_rows(app) == []  # 既有通道不产 counter_question.asked


# ---------------------------------------------------------------- §12.8 三分支


@pytest.mark.parametrize(
    "reason", ["model_none", "model_out_of_candidates", "model_failed"]
)
def test_a_refused_probe_branch_leaves_todays_shape_word_for_word(
    tmp_path: Path, reason: str
) -> None:
    """§12.8：NONE / 越界 / 模型失败都不反问，gap 键集逐字，成本照落。"""
    brain = _RefusingProbeBrain(reason)
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, QUESTION_TEXT, f"t6-refuse-{reason}")

    assert body["clarification"] is None
    assert body["reply"] == UNKNOWN_MENU_REPLY
    attributes = _understand(client, body["trace_id"])
    assert attributes["model_gap"] == TODAYS_QUESTION_GAP
    assert body["slots"]["counter_question_asked"] is False  # 上限位不被消耗
    assert _audit_rows(app) == []
    assert _chat_rows(app) == 1  # 拒答轮付了钱，成本行必须可从台账观察到


# ---------------------------------------------------------------- §12.10 三场景


def test_an_off_target_answer_after_a_counter_question_falls_back_today(
    tmp_path: Path,
) -> None:
    """§12.10/§6.3 答非所问：当普通新轮处理，不再反问、不死锁、共享预算 +1。"""
    brain = FixedBrain("POLICY_QA")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    first = _send(client, QUESTION_TEXT, "t6-offtarget")
    assert first["clarification"] is not None
    assert first["clarification"]["reason"] == COUNTER_REASON
    assert first["slots"]["clarification_count"] == 1  # §5.2：反问轮照常递增
    assert first["slots"]["counter_question_asked"] is True

    second = _send(client, OFF_TARGET_ANSWER, first["conversation_id"])
    assert second["clarification"] is None
    assert second["reply"] == UNKNOWN_MENU_REPLY
    assert len(_audit_rows(app)) == 1  # 不再多出一条反问审计
    assert _tasks(app) == 0  # 不凭空建任务


def test_a_refusal_then_explicit_handoff_is_never_blocked(tmp_path: Path) -> None:
    """§12.10/§6.3 拒绝：说「都不是」落今天兜底；随后说「转人工」既有通道照达。"""
    brain = FixedBrain("POLICY_QA")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    first = _send(client, QUESTION_TEXT, "t6-refusal")
    assert first["clarification"] is not None

    second = _send(client, REFUSAL_ANSWER, first["conversation_id"])
    assert second["clarification"] is None
    assert second["reply"] == UNKNOWN_MENU_REPLY
    assert _tasks(app) == 0  # 拒绝轮同样不凭空建任务

    third = _send(client, "转人工", second["conversation_id"])
    assert third["intent"] == "HUMAN_HANDOFF"
    assert third["handoff"]["signal_id"] == "user_request"
    assert (
        third["clarification"] is None
        or third["clarification"]["reason"] != COUNTER_REASON
    )
    assert len(_audit_rows(app)) == 1  # 反问审计只有第一轮那一条


def test_silence_after_a_counter_question_builds_nothing(tmp_path: Path) -> None:
    """§12.10/§6.3 沉默：客户不再发言，会话停在今日也会停的位置——零任务零移交。

    F-1 不引入超时机制（反问轮根本到不了写路径入口，§7），本用例把「沉默
    不产生任何新的副作用行」钉在库层面。
    """
    brain = FixedBrain("POLICY_QA")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, QUESTION_TEXT, "t6-silence")
    assert body["clarification"] is not None
    assert body["task"] is None

    with app.state.session_factory() as session:
        assert int(session.scalar(select(func.count()).select_from(HandoffEvent)) or 0) == 0
    assert _tasks(app) == 0
    rows = _audit_rows(app)
    assert len(rows) == 1  # 这一轮之后盘上只有那一条反问审计
