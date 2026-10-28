"""P8 留痕：坐席看得懂「这次回退是因为越界」，但 trace 里不躺原文。

候选证据串含用户消息内容，把它写进 trace 等于把原文搬进审计面；所以这里只允许出现
**数量**与**原因**。三条断言分别钉住对话侧、台账侧，以及「候选化不改采纳面」。

对话侧不会越界，这是闸门决定的：模型只在规则读不出、目录也没有 cue 时被问，那时候
候选集根本没收窄（整份目录都在）。所以「越界」出现在写路径——规则已经读出退款，
模型却答了个商品问题。也正因为候选集没收窄，「模型在候选里挑了 `TICKET_CREATE`」
是**合法回答**而不是越界；它照样开不出工单，这条是规格里「采纳面不变」的唯一对话侧证据。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent.candidates import build_candidates
from app.agent.intent_catalog import get_catalog
from app.agent.pydantic_agent import IntentAgent, IntentBrain, IntentOutput, ModelTrace
from app.agent.routing import classify
from app.core.config import Settings
from app.main import create_app
from app.models.task import Task, TaskEvent
from app.models.ticket import Ticket
from app.seed import seed_all
from tests.conftest import ALICE

# 规则读不出、目录也没有 cue 的一句话：人都知道这是要退款。
REFUND_GAP = "我申请退一部分款"
# 规则已经读出退款的一句话。
REFUND_READ = "我要申请退款，订单 ORD-DEMO-1001，金额 129 元"


class StubAgent:
    """A model that always answers with the same fixed reading."""

    model_name = "stub"

    def __init__(self, intent: str) -> None:
        self.intent = intent
        self.calls = 0

    def parse(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        candidates: Any = None,
    ) -> tuple[IntentOutput, ModelTrace]:
        self.calls += 1
        return (
            IntentOutput(
                intent=self.intent,
                order_id=None,
                amount_cents=None,
                confidence=0.9,
                decision_summary="固定读数。",
            ),
            ModelTrace(requests=1),
        )


def _app(tmp_path: Path, brain: IntentBrain) -> FastAPI:
    application = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+pysqlite:///{tmp_path / 'trace.db'}",
            auto_create_tables=True,
            agent_mode="pydantic_ai",
        ),
        brain=brain,
    )
    seed_all(application.state.session_factory)
    return application


def _understand(client: TestClient, trace_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/traces/{trace_id}", headers=ALICE)
    assert response.status_code == 200, response.text
    spans = [span for span in response.json()["spans"] if span["kind"] == "understand"]
    assert spans, f"no understand span in {trace_id}"
    return dict(spans[0]["attributes"])


def test_a_chat_turn_records_the_offered_count_and_no_evidence_strings(
    tmp_path: Path,
) -> None:
    """规则没依据时不收窄：留痕里的候选数就是整份目录的行数，模型答 NONE 也只是答案。"""
    agent = StubAgent("NONE")
    brain = IntentBrain(mode="pydantic_ai", agent=cast("IntentAgent", agent))
    client = TestClient(_app(tmp_path, brain))

    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": REFUND_GAP, "conversation_id": "cand-none"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    attributes = _understand(client, body["trace_id"])

    gap = attributes["model_gap"]
    assert gap["gate"] == "consult"
    assert gap["adopted"] is False
    assert gap["model"]["model_reason"] == "model_none"
    assert gap["model"]["model_candidates"] == len(get_catalog().specs)
    assert gap["model"]["model_requests"] == 1
    # 证据串里含用户原话：它不许出现在任何留痕面。
    assert "cue:" not in json.dumps(attributes, ensure_ascii=False)
    assert "rule:" not in json.dumps(attributes, ensure_ascii=False)


def test_the_task_ledger_shows_the_fallback_was_an_out_of_candidate_answer(
    tmp_path: Path,
) -> None:
    agent = StubAgent("PRODUCT_QUERY")
    brain = IntentBrain(mode="pydantic_ai", agent=cast("IntentAgent", agent))
    app = _app(tmp_path, brain)
    client = TestClient(app)

    created = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={"request": REFUND_READ, "context": {"refund_reason": "QUALITY"}},
    )
    assert created.status_code == 201, created.text
    task = created.json()
    attributes = _understand(client, task["trace_id"])

    assert attributes["model_reason"] == "model_out_of_candidates"
    # 越界不改结果：路由仍是规则读数。
    assert attributes["intent"] == "REFUND_REQUEST"
    assert attributes["model_candidates"] >= 1
    assert agent.calls == 1

    with app.state.session_factory() as session:
        row = session.execute(
            select(TaskEvent.payload)
            .join(Task, Task.id == TaskEvent.task_id)
            .where(Task.trace_id == task["trace_id"], TaskEvent.event_type == "task.routed")
        ).first()
    assert row is not None
    assert row[0]["model"]["model_reason"] == "model_out_of_candidates"


def test_an_in_candidate_ticket_reading_still_opens_no_ticket_at_the_chat_entry(
    tmp_path: Path,
) -> None:
    """规格里「采纳面保持不变」的对话侧证据：`TICKET_CREATE` 在候选里，也不建工单。

    这句话没有任何内容证据，所以候选集**没收窄**，整份目录的行名都交给模型 ——
    `TICKET_CREATE` 是模型**被允许说**的答案，`model_reason` 为空（不是越界）。
    采纳面照旧：对话入口只认退款读数，其余全部丢弃，工单表与任务表都还是空的。
    """
    rule_plan = classify(REFUND_GAP)
    offered = build_candidates(REFUND_GAP, None, rule_plan, catalog=get_catalog())
    assert offered.narrowed is False
    assert offered.allows("TICKET_CREATE") is True

    agent = StubAgent("TICKET_CREATE")
    brain = IntentBrain(mode="pydantic_ai", agent=cast("IntentAgent", agent))
    app = _app(tmp_path, brain)
    client = TestClient(app)

    response = client.post(
        "/api/v1/support/messages",
        headers=ALICE,
        json={"message": REFUND_GAP, "conversation_id": "cand-ticket"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert agent.calls == 1
    # 路由结果仍是规则读数，工单与任务都没被这条读数创建。
    assert body["intent"] == "UNKNOWN"
    assert body["task"] is None
    with app.state.session_factory() as session:
        assert int(session.scalar(select(func.count()).select_from(Ticket)) or 0) == 0
        assert int(session.scalar(select(func.count()).select_from(Task)) or 0) == 0

    gap = _understand(client, body["trace_id"])["model_gap"]
    assert gap["gate"] == "consult"
    assert gap["adopted"] is False
    # R7 收口后 `intent` 记的是**路由结果**（规则读数），模型那句 `TICKET_CREATE`
    # 由改道计数承接：合法、未被采纳、方向是「规则读意图 → 模型给写意图」。
    assert gap["intent"] == "UNKNOWN"
    assert gap["redirect"] is False
    assert gap["redirect_rejected"] is True
    assert gap["redirect_kind"] == "extra_write"
    assert gap["model"]["model_reason"] is None
    assert gap["model"]["model_requests"] == 1
    assert gap["model"]["model_candidates"] == len(get_catalog().specs)
