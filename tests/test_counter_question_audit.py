"""F1-T5 钉桩：反问审计事件 ``counter_question.asked`` 与回退开关。

口径 `app/agent/counter_question.yaml`：§8.2（审计恰一条、三个
detail 键、被拦不产）、§10（``counter_question_enabled`` 默认 true 的回退
等价）、§3.1 条件 2（开关关闭 = 触发条件不满足）、§9（rules 模式三不变式）。

钉住的最小事实：

1. 成功反问轮在审计表里**恰一行**，字段与 detail 键集逐字符合 §8.2；
2. 两个出口的 ``gate`` 分别是 ``"question"`` / ``"consult"``，
   ``reading_intent`` 是目录行 id（英文枚举名，不是客户原文）；
3. detail 序列化文本里没有客户原话片段、没有 ``ORD-`` 形态订单号；
4. 被拦路径（上限已用 / 探针 NONE / 入口守卫 / 缺标签 / 开关关 / rules
   模式）一行都不产；
5. 开关关闭时问句门**不问模型**（用量台账零 chat 行），回复逐字回落
   通用菜单，``model_gap`` 与 ``support.reply`` 键集不变，上限位仍 False；
6. 一问一停的既有会话里，审计行数不随第二轮增加（恒 == 1）。

模型不出现：问句门沿用 ``tests/test_llm_gap`` 的 ``FixedBrain``，consult
出口沿用 ``RecordingAgent`` + 真实 ``IntentBrain``——均为既有替身，本文件
不发明第二套假件，只新增开关关闭的 ``Settings`` 构造。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent.counter_question import get_counter_question
from app.core.config import Settings
from app.main import create_app
from app.models.audit import AuditEvent
from app.seed import seed_all
from tests.conftest import ALICE
from tests.test_consult_counter_question import (
    AS_DICT_KEYS,
    CONSULT_GAP,
    _consult_brain,
)
from tests.test_counter_question_turn import (
    ORDER_QUESTION_TEXT,
    QUESTION_TEXT,
    SECOND_QUESTION_TEXT,
    TODAYS_QUESTION_GAP,
    UNKNOWN_MENU_REPLY,
    _chat_rows,
    _rules_app,
    _send,
    _understand,
)
from tests.test_llm_gap import FixedBrain, _app

ACTION = "counter_question.asked"
AUDIT_DETAIL_KEYS = frozenset({"gate", "reading_intent", "option_count"})

# consult 出口的 ``model_gap`` 顶层键集（T4b 落地前后同一份，§10.2 逐字判据）。
CONSULT_GAP_KEYS = frozenset(TODAYS_QUESTION_GAP) | {"model"}
# ``support.reply`` span 的既有键集（同样不得因本次改动增减）。
REPLY_SPAN_KEYS = frozenset(
    {
        "intent",
        "reply_chars",
        "reply_sha256",
        "cards",
        "clarified",
        "clarify_reason",
        "clarify_slot",
        "clarify_candidates",
        "outcome",
        "action",
    }
)


def _flag_off_app(tmp_path: Path, brain: Any) -> FastAPI:
    """与 ``test_llm_gap._app`` 同形，只多一个关闭的反问开关。"""
    application = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+pysqlite:///{tmp_path / 'gap.db'}",
            auto_create_tables=True,
            agent_mode="pydantic_ai",
            counter_question_enabled=False,
        ),
        brain=brain,
    )
    seed_all(application.state.session_factory)
    return application


def _audit_rows(app: FastAPI) -> list[AuditEvent]:
    with app.state.session_factory() as session:
        return list(
            session.scalars(select(AuditEvent).where(AuditEvent.action == ACTION))
        )


def _reply_attributes(client: TestClient, trace_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/traces/{trace_id}", headers=ALICE)
    assert response.status_code == 200, response.text
    spans = [span for span in response.json()["spans"] if span["name"] == "support.reply"]
    assert len(spans) == 1, spans
    return dict(spans[0]["attributes"])


# ---------------------------------------------------------------- 成功轮契约


def test_a_successful_question_round_records_exactly_one_event(tmp_path: Path) -> None:
    """§8.2：一次反问恰一行，字段逐字：resource_type/outcome/actor/tenant/resource_id。"""
    brain = FixedBrain("POLICY_QA")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, QUESTION_TEXT, "t5-q-once")

    assert body["clarification"] is not None  # 前提：这一轮真的反问了
    rows = _audit_rows(app)
    assert len(rows) == 1
    row = rows[0]
    assert row.action == ACTION
    assert row.resource_type == "conversation"
    assert row.outcome == "SUCCESS"
    assert row.resource_id == body["conversation_id"]
    assert row.tenant_id == "demo"
    assert row.actor_id == "alice"


def test_the_question_exit_records_its_gate_and_reading(tmp_path: Path) -> None:
    """§8.2：detail 恰好三个键；question 出口记 ``gate="question"`` 与探针读数。"""
    brain = FixedBrain("POLICY_QA")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, QUESTION_TEXT, "t5-q-detail")

    (row,) = _audit_rows(app)
    assert frozenset(row.detail) == AUDIT_DETAIL_KEYS
    assert row.detail["gate"] == "question"
    assert row.detail["reading_intent"] == "POLICY_QA"
    assert row.detail["option_count"] == len(body["suggestions"])


def test_the_consult_exit_records_its_gate_and_reading(tmp_path: Path) -> None:
    """§8.2 的 consult 行：gate 与 reading_intent 取自同一次 plan 的带内读数。"""
    brain, _agent = _consult_brain("TICKET_CREATE")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, CONSULT_GAP, "t5-c-detail")

    assert body["clarification"] is not None
    (row,) = _audit_rows(app)
    assert frozenset(row.detail) == AUDIT_DETAIL_KEYS
    assert row.detail["gate"] == "consult"
    assert row.detail["reading_intent"] == "TICKET_CREATE"
    assert row.detail["option_count"] == len(body["suggestions"])


def test_the_question_detail_carries_no_customer_words_or_order_numbers(
    tmp_path: Path,
) -> None:
    """§8.2 内容红线：detail 序列化文本里既无原话片段也无 ``ORD-``。"""
    brain = FixedBrain("POLICY_QA")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    _send(client, QUESTION_TEXT, "t5-q-clean")

    (row,) = _audit_rows(app)
    text = json.dumps(row.detail, ensure_ascii=False)
    assert "ORD-" not in text
    for fragment in ("运费", "谁出"):
        assert fragment not in text


def test_the_consult_detail_carries_no_customer_words_or_order_numbers(
    tmp_path: Path,
) -> None:
    brain, _agent = _consult_brain("TICKET_CREATE")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    _send(client, CONSULT_GAP, "t5-c-clean")

    (row,) = _audit_rows(app)
    text = json.dumps(row.detail, ensure_ascii=False)
    assert "ORD-" not in text
    for fragment in ("申请", "退一部分款"):
        assert fragment not in text


# ---------------------------------------------------------------- 被拦路径


def test_a_refused_probe_records_nothing(tmp_path: Path) -> None:
    """§8.2：形成侧被拦（NONE 拒答）→ 零审计，即使这一轮付了探针钱。"""
    brain = FixedBrain("UNKNOWN")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, QUESTION_TEXT, "t5-refused")

    assert body["clarification"] is None
    assert _audit_rows(app) == []
    assert _chat_rows(app) == 1  # 付过的钱照旧可从台账观察


def test_the_second_round_under_the_cap_records_nothing(tmp_path: Path) -> None:
    """§8.2 + 一问一停：第二轮被上限拦下，审计行数不增加（恒 == 1）。"""
    brain = FixedBrain("POLICY_QA")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    first = _send(client, QUESTION_TEXT, "t5-cap")
    second = _send(client, SECOND_QUESTION_TEXT, first["conversation_id"])

    assert first["clarification"] is not None
    assert second["clarification"] is None
    assert second["reply"] == UNKNOWN_MENU_REPLY
    rows = _audit_rows(app)
    assert len(rows) == 1


def test_a_nonunknown_landing_records_nothing(tmp_path: Path) -> None:
    """§3.3 入口守卫轮（ORDER_QUERY 落点）：不形成也不写审计。"""
    brain = FixedBrain("POLICY_QA")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, ORDER_QUESTION_TEXT, "t5-landing")

    assert body["intent"] == "ORDER_QUERY"
    assert body["clarification"] is None
    assert _audit_rows(app) == []


def test_a_missing_label_round_pays_but_records_nothing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """§4.7/§8.2：标签凑不齐 → 草稿不形成；调用已付，成本行照落，审计零行。"""
    spec = get_counter_question()
    unlabeled = replace(
        spec, labels={k: v for k, v in spec.labels.items() if k != "POLICY_QA"}
    )
    monkeypatch.setattr("app.agent.support_agent.get_counter_question", lambda: unlabeled)
    brain = FixedBrain("POLICY_QA")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, QUESTION_TEXT, "t5-nolabel")

    assert body["clarification"] is None
    assert body["reply"] == UNKNOWN_MENU_REPLY
    attributes = _understand(client, body["trace_id"])
    assert attributes["model_gap"] == TODAYS_QUESTION_GAP
    assert _audit_rows(app) == []
    assert _chat_rows(app) == 1


def test_rules_mode_records_nothing(tmp_path: Path) -> None:
    """§9：rules 模式三不变式之一——零审计行。"""
    brain = FixedBrain("POLICY_QA")
    app = _rules_app(tmp_path, brain)
    client = TestClient(app)

    _send(client, QUESTION_TEXT, "t5-rules")

    assert _audit_rows(app) == []
    assert _chat_rows(app) == 0


# ---------------------------------------------------------------- 开关等价


def test_the_flag_defaults_to_true() -> None:
    """§10：默认开启——不加环境变量就是 F-1 落地后的行为。"""
    assert Settings().counter_question_enabled is True


def test_flag_off_question_gate_never_asks_the_model(tmp_path: Path) -> None:
    """§10.2「问句门不问模型」：零用量行、逐字菜单、零审计、上限位不被消耗。"""
    brain = FixedBrain("POLICY_QA")
    app = _flag_off_app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, QUESTION_TEXT, "t5-off-q")

    assert body["clarification"] is None
    assert body["reply"] == UNKNOWN_MENU_REPLY
    assert body["slots"]["counter_question_asked"] is False
    attributes = _understand(client, body["trace_id"])
    assert attributes["model_gap"] == TODAYS_QUESTION_GAP  # 逐字回今天形态
    assert _chat_rows(app) == 0  # 探针一次都没被叫
    assert brain.requests == []
    assert _audit_rows(app) == []


def test_flag_off_consult_exit_falls_back_to_the_generic_menu(tmp_path: Path) -> None:
    """§10.2：consult 出口回通用菜单——``plan`` 照常被问（不扩大回退面），零审计。"""
    brain, agent = _consult_brain("TICKET_CREATE")
    app = _flag_off_app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, CONSULT_GAP, "t5-off-c")

    assert len(agent.calls) == 1  # 回退的是反问，不是整个 consult 门
    assert body["clarification"] is None
    assert body["reply"] == UNKNOWN_MENU_REPLY
    assert body["slots"]["counter_question_asked"] is False
    assert _audit_rows(app) == []


def test_flag_off_keeps_the_trace_key_sets_unchanged(tmp_path: Path) -> None:
    """§10.2：``model_gap`` 与 ``support.reply`` 的键集逐字不变；计数键语义不动。"""
    brain, _agent = _consult_brain("TICKET_CREATE")
    app = _flag_off_app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, CONSULT_GAP, "t5-off-keys")

    attributes = _understand(client, body["trace_id"])
    gap = dict(attributes["model_gap"])
    assert frozenset(gap) == CONSULT_GAP_KEYS
    assert gap["gate"] == "consult"
    assert gap["redirect_rejected"] is True  # 判定照常，只是不反问
    model = dict(gap["model"])
    assert frozenset(model) <= AS_DICT_KEYS
    assert frozenset(_reply_attributes(client, body["trace_id"])) == REPLY_SPAN_KEYS
