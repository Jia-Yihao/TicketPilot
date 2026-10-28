"""P8 接线：`context:` 与 `order:` 证据必须在生产链路里真的出现。

口径：`.qoder/rules/intent-candidates.md` §2.2「生产接线口径」——调用方把会话
上下文（`pending_intent` / `last_intent`，值取 `ConversationSlots` 同名字段，缺任一
传 `None`）与订单状态（缺则 `None`）**真实传入** `build_candidates`；内核不查库、
不补取，也不得用默认值冒充「有证据」。本文件只钉接线：内核 (`candidates.py`) 已
另行验收，这里不修改、只经真实 `IntentBrain` 驱动。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent import pydantic_agent
from app.agent.candidates import CandidateSet, build_candidates
from app.agent.pydantic_agent import IntentAgent, IntentBrain, IntentOutput, ModelTrace
from app.core.config import Settings
from app.main import create_app
from app.models.llm_usage import LlmUsageRecord
from app.seed import seed_all
from tests.conftest import ALICE, BOB, CAROL

# 规则读不出、且带明确请求框架的一句话：对话入口在这里才会问模型。
CONSULT = "ORD-DEMO-1001 那笔钱 129 元给我退回来"
# 规则与 cue 同时失明的一句话；在 brain 层直接喂给 plan，不经对话闸门。
NO_CUE = "嗯那个事情"


class PointAgent:
    """假模型：原样记录 brain 递进来的候选集，答案固定。"""

    model_name = "stub:wiring"

    def __init__(self, intent: str = "NONE") -> None:
        self.calls: list[dict[str, Any]] = []
        self._output = IntentOutput(
            intent=cast(Any, intent),
            order_id=None,
            amount_cents=None,
            confidence=0.5,
            decision_summary="stub",
        )

    def parse(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        candidates: object = None,
    ) -> tuple[IntentOutput, ModelTrace]:
        self.calls.append({"request": request, "context": context, "candidates": candidates})
        return self._output, ModelTrace(requests=1)


def _brain(agent: PointAgent) -> IntentBrain:
    return IntentBrain(mode="pydantic_ai", agent=cast(IntentAgent, agent))


def _app(tmp_path: Path, brain: IntentBrain, *, agent_mode: str = "pydantic_ai") -> FastAPI:
    application = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+pysqlite:///{tmp_path / 'wiring.db'}",
            auto_create_tables=True,
            agent_mode=agent_mode,
        ),
        brain=brain,
    )
    seed_all(application.state.session_factory)
    return application


def _message(client: TestClient, headers: dict[str, str], text: str, **extra: object) -> dict:
    response = client.post(
        "/api/v1/support/messages",
        headers=headers,
        json={"message": text, **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _spy(monkeypatch) -> list[dict[str, Any]]:  # noqa: ANN001 - pytest fixture
    """把真实内核包一层，记录每次调用的实参；计算结果仍由内核给出。"""
    seen: list[dict[str, Any]] = []
    real = build_candidates

    def spy(message: str, context: Any, route: Any, **kwargs: Any) -> CandidateSet:
        seen.append(
            {
                "message": message,
                "context": dict(context or {}),
                "kwargs": dict(kwargs),
            }
        )
        return real(message, context, route, **kwargs)

    monkeypatch.setattr(pydantic_agent, "build_candidates", spy)
    return seen


def _evidence_for(candidate_set: CandidateSet, intent_id: str) -> tuple[str, ...]:
    return next(c.evidence for c in candidate_set.candidates if c.intent_id == intent_id)


def _all_evidence(candidate_set: CandidateSet) -> list[str]:
    return [evidence for candidate in candidate_set.candidates for evidence in candidate.evidence]


def _usage(app: FastAPI) -> int:
    with app.state.session_factory() as session:
        return int(session.scalar(select(func.count()).select_from(LlmUsageRecord)) or 0)


# ------------------------------------------------------------------ brain 层：实参转发
def test_plan_forwards_order_state_to_build_candidates(monkeypatch) -> None:  # noqa: ANN001
    seen = _spy(monkeypatch)
    brain = _brain(PointAgent())

    _plan, source, _trace = brain.plan(
        CONSULT, {"order_id": "ORD-DEMO-1001"}, order_state="DELIVERED"
    )

    assert source == "pydantic_ai"
    assert seen, "候选路径必须被走到"
    assert seen[-1]["kwargs"].get("order_state") == "DELIVERED"


def test_plan_defaults_order_state_to_none_for_existing_callers(monkeypatch) -> None:  # noqa: ANN001
    """默认值必须是 None：runtime.py 那个调用点没传这个参数，也不得崩。"""
    seen = _spy(monkeypatch)
    brain = _brain(PointAgent())

    brain.plan(CONSULT, {"order_id": "ORD-DEMO-1001"}, decision=None)

    assert seen[-1]["kwargs"].get("order_state") is None


def test_caller_supplied_order_state_appears_as_order_evidence() -> None:
    agent = PointAgent()
    brain = _brain(agent)

    brain.plan(CONSULT, {"order_id": "ORD-DEMO-1001"}, order_state="DELIVERED")

    candidates = agent.calls[-1]["candidates"]
    assert isinstance(candidates, CandidateSet)
    assert "order:DELIVERED" in _all_evidence(candidates)


def test_missing_order_state_adds_no_order_evidence() -> None:
    agent = PointAgent()
    brain = _brain(agent)

    brain.plan(CONSULT, {"order_id": "ORD-DEMO-1001"})

    candidates = agent.calls[-1]["candidates"]
    assert isinstance(candidates, CandidateSet)
    assert [e for e in _all_evidence(candidates) if e.startswith("order:")] == []


def test_slot_context_keys_appear_as_context_evidence() -> None:
    agent = PointAgent()
    brain = _brain(agent)

    brain.plan(NO_CUE, {"pending_intent": "ORDER_QUERY", "last_intent": None})

    candidates = agent.calls[-1]["candidates"]
    assert isinstance(candidates, CandidateSet)
    assert "context:pending_intent" in _evidence_for(candidates, "ORDER_QUERY")
    assert [e for e in _all_evidence(candidates) if e == "context:last_intent"] == []


def test_none_slot_values_do_not_fabricate_context_evidence() -> None:
    """键在但值为 None = 没有该证据；不得被读成「有证据」。"""
    agent = PointAgent()
    brain = _brain(agent)

    brain.plan(NO_CUE, {"pending_intent": None, "last_intent": None})

    candidates = agent.calls[-1]["candidates"]
    assert isinstance(candidates, CandidateSet)
    assert [e for e in _all_evidence(candidates) if e.startswith("context:")] == []


# ------------------------------------------------------------------ 对话链路：接线真的发生
def test_chat_turn_passes_slot_context_into_candidates(tmp_path: Path) -> None:
    """第一轮缺槽位留下 pending_intent，第二轮问模型时它必须变成真证据。"""
    agent = PointAgent()
    app = _app(tmp_path, _brain(agent))
    client = TestClient(app)

    first = _message(client, ALICE, "帮我查一下订单")
    assert first["clarification"] is not None
    conversation_id = first["conversation_id"]

    calls_before = len(agent.calls)
    second = _message(client, ALICE, CONSULT, conversation_id=conversation_id)
    assert second is not None

    assert len(agent.calls) > calls_before, "consult 回合必须问了模型"
    candidates = agent.calls[-1]["candidates"]
    assert isinstance(candidates, CandidateSet)
    assert "context:pending_intent" in _evidence_for(candidates, "ORDER_QUERY")


def test_chat_turn_without_slots_has_no_context_evidence(tmp_path: Path) -> None:
    """全新会话（无槽位）：键可以存在但值为 None，候选里不得出现 context: 证据。"""
    agent = PointAgent()
    app = _app(tmp_path, _brain(agent))
    client = TestClient(app)

    _message(client, ALICE, CONSULT)

    candidates = agent.calls[-1]["candidates"]
    assert isinstance(candidates, CandidateSet)
    assert [e for e in _all_evidence(candidates) if e.startswith("context:")] == []


def test_the_owners_order_state_reaches_the_candidates(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    seen = _spy(monkeypatch)
    app = _app(tmp_path, _brain(PointAgent()))
    client = TestClient(app)

    _message(client, ALICE, CONSULT)

    assert seen[-1]["kwargs"].get("order_state") == "DELIVERED"


def test_a_foreign_order_supplies_no_order_state(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """同租户不同用户：读别人的订单等于读不到，order_state 必须是 None。"""
    seen = _spy(monkeypatch)
    agent = PointAgent()
    app = _app(tmp_path, _brain(agent))
    client = TestClient(app)

    _message(client, BOB, CONSULT)

    assert seen[-1]["kwargs"].get("order_state") is None
    candidates = agent.calls[-1]["candidates"]
    assert isinstance(candidates, CandidateSet)
    assert [e for e in _all_evidence(candidates) if e.startswith("order:")] == []


def test_a_cross_tenant_order_supplies_no_order_state(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    seen = _spy(monkeypatch)
    app = _app(tmp_path, _brain(PointAgent()))
    client = TestClient(app)

    _message(client, CAROL, CONSULT)

    assert seen[-1]["kwargs"].get("order_state") is None


def test_an_unknown_order_does_not_break_the_turn(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """订单号查不到：不抛错、不打断这一轮，只是没有 order: 证据。"""
    seen = _spy(monkeypatch)
    agent = PointAgent()
    app = _app(tmp_path, _brain(agent))
    client = TestClient(app)

    body = _message(client, ALICE, "ORD-GHOST-9999 那笔钱 129 元给我退回来")

    assert body["reply"]
    assert seen[-1]["kwargs"].get("order_state") is None


# ------------------------------------------------------------------ rules 模式：整条链路零调用
def test_rules_mode_never_reaches_the_wiring(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """AGENT_MODE=rules：不进候选路径——零 build_candidates、零模型调用、零用量行。"""
    seen = _spy(monkeypatch)
    agent = PointAgent()
    app = _app(tmp_path, _brain(agent), agent_mode="rules")
    client = TestClient(app)

    _message(client, ALICE, CONSULT)

    assert seen == []
    assert agent.calls == []
    assert _usage(app) == 0
