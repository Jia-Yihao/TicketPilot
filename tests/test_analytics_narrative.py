"""The expression path: facts in, a sentence out, and nothing else.

The model is never asked to compute. It receives the server's aggregate and
the recent detail summaries, and must return a sentence whose numbers already
exist in those facts. Everything here is deterministic except the narrator,
which the tests replace with a stub.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.analytics_narrator import build_analytics_narrator
from app.core.config import Settings
from app.sql.narrative import (
    FACTUALITY_CONSTRAINT,
    AnalyticsNarrator,
    NarrationCall,
    NarrationPrompt,
    build_prompt,
    narrate,
    render_template,
)
from app.sql.snapshot import AnalyticsSnapshot, FactGroup, RecentFact

NOW = datetime(2026, 10, 23, 12, 0, 0)


class StubNarrator:
    """Records what it was asked and returns a canned sentence."""

    model_name = "stub"

    def __init__(self, reply: str | None = "上个月共有 2 笔退款，合计 209.00 元。") -> None:
        self.reply = reply
        self.prompts: list[NarrationPrompt] = []

    def narrate(self, prompt: NarrationPrompt) -> tuple[str | None, NarrationCall]:
        self.prompts.append(prompt)
        return self.reply, NarrationCall(requests=1, input_tokens=120, output_tokens=30)


class FailingNarrator:
    model_name = "failing"

    def narrate(self, prompt: NarrationPrompt) -> tuple[str | None, NarrationCall]:
        raise RuntimeError("model unavailable")


def _snapshot(**overrides: Any) -> AnalyticsSnapshot:
    values: dict[str, Any] = {
        "query_id": "qry_test",
        "dataset": "refunds",
        "label": "退款",
        "unit": "笔",
        "metric": "count_and_sum",
        "group_by": None,
        "time_range": "上个月",
        "count": 2,
        "total_cents": 20900,
        "groups": (),
        "recent": (
            RecentFact(
                at="2026-09-20T10:00:00",
                status="已到账",
                amount_cents=12900,
                reference="RF-1001",
            ),
            RecentFact(
                at="2026-09-12T09:00:00",
                status="已到账",
                amount_cents=8000,
                reference="RF-1002",
            ),
        ),
        "rows": ({"id": "1", "amount_cents": 12900}, {"id": "2", "amount_cents": 8000}),
        "total_rows": 2,
        "truncated": False,
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=15),
    }
    values.update(overrides)
    return AnalyticsSnapshot(**values)


# ------------------------------------------------------------------- prompt
def test_the_prompt_carries_facts_and_the_question_only() -> None:
    snapshot = _snapshot()
    prompt = build_prompt("我上个月退了几笔款，多少钱", snapshot)

    assert FACTUALITY_CONSTRAINT in prompt.system
    assert "我上个月退了几笔款，多少钱" in prompt.user
    assert "209.00" in prompt.user or "209" in prompt.user
    lowered = prompt.text.lower()
    for forbidden in ("select", "from ", "tenant_id", "user_id", "orders", "refunds"):
        assert forbidden not in lowered
    assert "rows" not in json.loads(prompt.user)["facts"]


def test_the_prompt_stays_inside_the_character_budget() -> None:
    recent = tuple(
        RecentFact(
            at=f"2026-09-{day:02d}T10:00:00",
            status="已到账",
            amount_cents=1000 + day,
            reference=f"RF-{day:04d}",
        )
        for day in range(1, 15)
    )
    groups = tuple(
        FactGroup(bucket=f"2026-{month:02d}", label=f"2026-{month:02d}", count=3, total_cents=500)
        for month in range(1, 13)
    )
    snapshot = _snapshot(recent=recent, groups=groups, total_rows=14)
    prompt = build_prompt("按月统计我的退款", snapshot)

    assert prompt.facts_chars <= snapshot.context_budget
    facts = json.loads(prompt.user)["facts"]
    assert "rows" not in facts
    assert facts["count"] == 2


def test_l3_rows_never_reach_the_prompt() -> None:
    snapshot = _snapshot(
        rows=tuple({"id": f"row-{index}"} for index in range(50)),
        total_rows=50,
        truncated=True,
    )
    prompt = build_prompt("我的退款明细", snapshot)
    facts = json.loads(prompt.user)["facts"]

    assert "rows" not in facts
    assert "row-0" not in prompt.text
    assert facts["note"].startswith("只展开最近")


# --------------------------------------------------------------- expression
def test_a_matching_sentence_is_used() -> None:
    narrator = StubNarrator()
    result = narrate("我上个月退了几笔款，多少钱", _snapshot(), narrator)

    assert result.source == "model"
    assert result.reply == "上个月共有 2 笔退款，合计 209.00 元。"
    assert len(narrator.prompts) == 1
    assert result.faithfulness == "ok"


def test_without_a_narrator_the_template_is_used() -> None:
    result = narrate("我上个月退了几笔款，多少钱", _snapshot(), None)

    assert result.source == "template"
    assert result.faithfulness == "skipped"
    assert "2" in result.reply
    assert "209.00" in result.reply


def test_a_failing_narrator_falls_back_to_the_template() -> None:
    result = narrate("我上个月退了几笔款，多少钱", _snapshot(), FailingNarrator())

    assert result.source == "template"
    assert result.call is not None
    assert result.call.status == "FAILED"
    assert "2" in result.reply


def test_a_model_that_says_nothing_falls_back() -> None:
    result = narrate("我上个月退了几笔款，多少钱", _snapshot(), StubNarrator("  "))

    assert result.source == "template"
    assert "2" in result.reply


# -------------------------------------------------------------------- modes
def test_rules_mode_builds_no_narrator() -> None:
    assert build_analytics_narrator(Settings(agent_mode="rules")) is None


def test_turning_narration_off_builds_no_narrator() -> None:
    settings = Settings(
        agent_mode="pydantic_ai", analytics_narration_enabled=False
    )
    assert build_analytics_narrator(settings) is None


# ---------------------------------------------------------------- same shape
def test_both_paths_render_the_same_template() -> None:
    snapshot = _snapshot()
    sentence, fields = render_template(
        dataset="refunds",
        metric="count_and_sum",
        group_by=None,
        span="上个月",
        rows=snapshot.l1_rows(),
    )
    assert sentence == narrate("我上个月退了几笔款，多少钱", snapshot, None).reply
    assert fields["records"] == 2
    assert fields["amount_cents"] == 20900
    assert fields["range"] == "上个月"


# ----------------------------------------------------------- the agent path
def test_the_support_turn_uses_the_injected_narrator(app_factory) -> None:  # type: ignore[no-untyped-def]
    application: FastAPI = app_factory()
    stub = StubNarrator("上个月共有 2 笔退款，合计 209.00 元。")
    application.state.support_agent.narrator = stub
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/support/messages",
            json={"message": "我买了多少订单"},
            headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert len(stub.prompts) == 1
    assert payload["reply"] == "上个月共有 2 笔退款，合计 209.00 元。"
    assert payload["context_cards"][0]["kind"] == "analytics"


def test_a_refused_question_never_reaches_the_narrator(client: TestClient) -> None:
    response = client.post(
        "/api/v1/support/messages",
        json={"message": "我的订单有多少笔已到账"},
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert response.status_code == 200
    assert "转人工" in response.json()["reply"]


def test_the_rules_app_has_no_narrator(client: TestClient) -> None:
    application = client.app
    assert application.state.support_agent.narrator is None


@pytest.mark.parametrize("enabled", [True, False])
def test_the_narrator_is_configuration_only(enabled: bool) -> None:
    settings = Settings(
        agent_mode="pydantic_ai" if enabled else "rules",
        analytics_narration_enabled=enabled,
    )
    narrator: AnalyticsNarrator | None = build_analytics_narrator(settings)
    assert (narrator is not None) is enabled
