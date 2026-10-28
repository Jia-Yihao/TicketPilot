"""F-1 T4 钉桩：问句门的「可以问，但只换来一次反问」。

口径 ``.qoder/rules/clarify-counter-question.md``：§3 触发、§4 选项契约、
§5.1 单轮上限、§6.1 反问 Draft 形态、§2.2 四计数键不落盘。完整矩阵属 T6
（任务单 #29），这里只钉 T4 改动面必须成立的六条最小事实：

1. 触发满足时产出一次反问（澄清四键、上限置位、尾句复用）；
2. 同一会话顺序第二轮不再反问，回复与 gap 回落到落地前形态；
3. 探针拒答时 gap 与今天的固定字面量逐字相等，但成本行照落；
4. 反问轮不产生 ``ModelDecision``（无任务），trace 里检索不到四个 ``model_*`` 计数键；
5. ``AGENT_MODE=rules`` 下反问不可达：零模型调用、回复逐字不变；
6. 规则读数非 UNKNOWN 的问句支轮次不进探针：零调用、零成本、gap 逐字、
   上限位不被消费（§3.3 反问只替代 UNKNOWN 兜底菜单）。

模型不出现：沿用 ``tests/test_llm_gap`` 的 ``FixedBrain`` 替身。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent.counter_question import get_counter_question
from app.core.config import Settings
from app.main import create_app
from app.models.llm_usage import LlmUsageRecord
from app.seed import seed_all
from tests.conftest import ALICE
from tests.test_llm_gap import FixedBrain, _app, _tasks

QUESTION_TEXT = "运费谁出？"
SECOND_QUESTION_TEXT = "我买的东西还没到，能等到几号"
# 问句门的第二从句：带订单号被规则读成 ORDER_QUERY，但仍是问句形态。
ORDER_QUESTION_TEXT = "ORD-DEMO-1002 怎么回事"

# 落地前问句支的固定 gap（口径 §3.2「没成反问就不许改键集」的钉桩副本）。
TODAYS_QUESTION_GAP = {
    "source": "rules",
    "intent": "UNKNOWN",
    "adopted": False,
    "gate": "question",
    "redirect": False,
    "redirect_rejected": False,
    "redirect_kind": "none",
}

# ``actions.unknown`` today's menu, copied verbatim: rules 模式的逐字对照。
UNKNOWN_MENU_REPLY = (
    "我可以帮你查商品信息、售后条款、订单与物流，"
    "也可以按你的订单判断退款资格，"
    "再把退款或工单请求交给确定性工作流。请补充商品名、SKU 或订单号。"
)


def _rules_app(tmp_path: Path, brain: Any) -> FastAPI:
    application = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+pysqlite:///{tmp_path / 'cq-rules.db'}",
            auto_create_tables=True,
            agent_mode="rules",
        ),
        brain=brain,
    )
    seed_all(application.state.session_factory)
    return application


def _send(client: TestClient, message: str, conversation_id: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": message, "conversation_id": conversation_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _understand(client: TestClient, trace_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/traces/{trace_id}", headers=ALICE)
    assert response.status_code == 200, response.text
    spans = [span for span in response.json()["spans"] if span["kind"] == "understand"]
    assert spans, f"no understand span in {trace_id}"
    return dict(spans[0]["attributes"])


def _chat_rows(app: FastAPI) -> int:
    with app.state.session_factory() as session:
        return len(
            list(
                session.scalars(
                    select(LlmUsageRecord).where(LlmUsageRecord.source == "chat")
                )
            )
        )


def test_a_triggered_question_round_asks_once(tmp_path: Path) -> None:
    """§3/§6.1：合法读数换来一次反问，四键与呈现契约照口径落地。"""
    brain = FixedBrain("POLICY_QA")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, QUESTION_TEXT, "cq-once")

    assert body["intent"] == "UNKNOWN"  # Draft 用规则读数，不是探针裸名
    clarification = body["clarification"]
    assert clarification is not None
    assert clarification["reason"] == "COUNTER_QUESTION_INTENT_AMBIGUOUS"
    assert clarification["prompt"] == get_counter_question().prompt_template
    assert clarification["expected_slot"] is None
    assert clarification["resumable_intent"] is None  # §6.1 定死 None，不是「假值」
    spec = get_counter_question()
    assert 0 < len(body["suggestions"]) <= spec.max_options
    assert body["reply"].endswith("你也可以直接说「转人工」，我会转给人工同事接手。")
    # §5.1：反问文本已生成 → 置位随本回合唯一一次槽位写回落库。
    assert body["slots"]["counter_question_asked"] is True
    assert brain.requests == []  # 问句支完全不调 plan


def test_the_cap_blocks_the_second_sequential_question(tmp_path: Path) -> None:
    """§5.1 可判定语义：顺序第二轮再满足触发也不反问，回复与 gap 回到今天形态。"""
    brain = FixedBrain("POLICY_QA")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    first = _send(client, QUESTION_TEXT, "cq-cap")
    assert first["clarification"] is not None

    # 同一会话：续跑要带回服务端分配的会话 id（``resolve`` 只认调用者自己的会话）。
    second = _send(client, SECOND_QUESTION_TEXT, first["conversation_id"])
    assert second["clarification"] is None
    assert second["reply"] == UNKNOWN_MENU_REPLY
    assert second["slots"]["counter_question_asked"] is True  # carried 位不被自嘲清回
    attributes = _understand(client, second["trace_id"])
    assert attributes["model_gap"] == TODAYS_QUESTION_GAP


def test_a_nonunknown_rule_reading_never_pays_the_probe(tmp_path: Path) -> None:
    """§3.3 入口守卫：规则读数非 UNKNOWN 的问句支轮次不进探针——零模型调用、
    零成本、gap 逐字今天形态、上限位不被消费（否则同一会话每轮都白付一次探针）。"""
    brain = FixedBrain("POLICY_QA")  # 替身读数会凑成反问——正因如此才要守卫
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, ORDER_QUESTION_TEXT, "cq-guard")

    assert body["intent"] == "ORDER_QUERY"  # 规则读数，不是探针裸名
    assert body["clarification"] is None  # 反问没有替换任何东西
    attributes = _understand(client, body["trace_id"])
    assert attributes["model_gap"] == TODAYS_QUESTION_GAP  # 逐字，source 未变
    assert brain.requests == []  # 本轮模型一次都没被叫到
    assert body["slots"]["counter_question_asked"] is False  # 上限位不被消耗


def test_a_refused_probe_keeps_todays_gap_word_for_word(tmp_path: Path) -> None:
    """§3.2/§10.2：没成反问就不改 gap 的键集——探针付了钱也一样。"""
    brain = FixedBrain("UNKNOWN")  # 替身的默认读数 → model_unknown 拒答
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, QUESTION_TEXT, "cq-refused")

    assert body["clarification"] is None
    assert body["reply"] == UNKNOWN_MENU_REPLY
    attributes = _understand(client, body["trace_id"])
    assert attributes["model_gap"] == TODAYS_QUESTION_GAP
    # 拒答轮**要**产成本行：探针付了钱就必须可从台账观察到（口径 §3.2/§13.3）。
    assert _chat_rows(app) == 1


def test_a_question_round_records_no_adoption_verdict(tmp_path: Path) -> None:
    """§2.2/DoD 5：反问轮不产 ModelDecision，四计数键不落盘。"""
    brain = FixedBrain("POLICY_QA")
    app = _app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, QUESTION_TEXT, "cq-keys")

    assert body["task"] is None
    assert _tasks(app) == 0  # 决策只随任务入账；问句门没有可入账的决策
    attributes = _understand(client, body["trace_id"])
    model = dict(attributes["model_gap"]["model"])
    for key in (
        "model_adopted",
        "model_redirect",
        "model_redirect_rejected",
        "model_redirect_kind",
    ):
        assert key not in model  # None = 没判过，as_dict 自然不写键


def test_rules_mode_never_reaches_the_counter_question(tmp_path: Path) -> None:
    """§9：rules 模式下反问不可达——零模型调用，回复与落地前逐字相同。"""
    brain = FixedBrain("POLICY_QA")
    app = _rules_app(tmp_path, brain)
    client = TestClient(app)

    body = _send(client, QUESTION_TEXT, "cq-rules")

    assert body["reply"] == UNKNOWN_MENU_REPLY
    assert body["clarification"] is None
    assert body["slots"]["counter_question_asked"] is False
    attributes = _understand(client, body["trace_id"])
    assert attributes["model_gap"] is None
    assert _chat_rows(app) == 0
