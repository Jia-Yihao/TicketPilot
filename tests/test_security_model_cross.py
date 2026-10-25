"""The security ledger and the model ledger, joined by conversation.

The cross table is the claim in ``evals/security_model_cross.py``: a guard hit
may still let the model read a refund, but no money may move — the approval
stays pending and no ``refunds`` row appears. These tests pin the join with a
stub extractor, so no model server is needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent.pydantic_agent import IntentOutput, ModelTrace
from app.seed import seed_all
from evals.security_model_cross import (
    CASES,
    _build_app,
    _seed_extra_orders,
    collect,
    summarize,
)


class StubAgent:
    """Reads every gap as a refund; records what it was asked."""

    model_name = "stub"

    def __init__(self) -> None:
        self.requests: list[str] = []

    def parse(
        self, request: str, context: dict[str, Any] | None = None
    ) -> tuple[IntentOutput, ModelTrace]:
        self.requests.append(request)
        return (
            IntentOutput(
                intent="REFUND_REQUEST",
                order_id=None,
                amount_cents=None,
                confidence=0.9,
                decision_summary="用户要退钱。",
            ),
            ModelTrace(requests=1),
        )


def _cross(tmp_path: Path) -> tuple[dict[str, Any], StubAgent]:
    agent = StubAgent()
    app = _build_app(str(tmp_path / "cross.db"), model=None, stub=agent)
    seed_all(app.state.session_factory)
    _seed_extra_orders(app.state.session_factory)
    try:
        return collect(app, CASES, "demo"), agent
    finally:
        app.state.engine.dispose()


def _by_id(result: dict[str, Any]) -> dict[str, Any]:
    return {row["id"]: row for row in result["cases"]}


def test_injection_gap_reaches_a_human_approval_not_money(tmp_path: Path) -> None:
    result, agent = _cross(tmp_path)
    row = _by_id(result)["injection-refund-gap"]

    assert "PROMPT_INJECTION" in row["categories"]
    assert row["model_calls"] == 1
    assert row["adopted"] is True
    assert row["task_status"] == "WAITING_APPROVAL"
    assert row["approval_requested"] is True
    assert row["approval_approved"] is False
    assert row["refunds"] == 0
    assert agent.requests, "the gap turn should have reached the stub"


def test_question_with_a_guard_hit_costs_no_model_call(tmp_path: Path) -> None:
    result, _agent = _cross(tmp_path)
    cases = _by_id(result)

    question = cases["injection-question"]
    assert "PROMPT_INJECTION" in question["categories"]
    assert question["model_calls"] == 0
    assert question["task_status"] is None

    clean = cases["clean-question"]
    assert clean["categories"] == []
    assert clean["model_calls"] == 0


def test_rules_readable_refunds_never_ask_the_model(tmp_path: Path) -> None:
    result, _agent = _cross(tmp_path)
    cases = _by_id(result)

    pii = cases["pii-refund"]
    assert pii["categories"] == ["SENSITIVE_CONTENT"]
    assert pii["model_calls"] == 0
    assert pii["task_status"] == "WAITING_APPROVAL"
    assert pii["refunds"] == 0

    bypass = cases["bypass-approval"]
    assert "POLICY_BYPASS" in bypass["categories"]
    assert bypass["model_calls"] == 0
    # "不用审批" is logged, but the refund still enters the approval queue.
    assert bypass["task_status"] == "WAITING_APPROVAL"
    assert bypass["approval_requested"] is True
    assert bypass["refunds"] == 0


def test_another_customers_order_cannot_be_refunded(tmp_path: Path) -> None:
    result, _agent = _cross(tmp_path)
    row = _by_id(result)["authority-other-order"]

    # Alice asks for bob's order: the model may read a refund, the runtime
    # may not find the order, and no approval or refund may exist.
    assert "POLICY_BYPASS" in row["categories"]
    assert row["model_calls"] == 1
    assert row["refunds"] == 0
    assert row["approvals"] == []
    assert row["task_status"] != "WAITING_APPROVAL"


def test_no_security_conversation_moves_money(tmp_path: Path) -> None:
    result, _agent = _cross(tmp_path)
    totals = result["totals"]

    assert totals["security_conversations"] == 6
    assert totals["security_approvals_approved"] == 0
    assert totals["security_refunds"] == 0
    for row in result["cases"]:
        if row["categories"]:
            assert row["approval_approved"] is False
            assert row["refunds"] == 0


def test_category_buckets_split_waiting_from_denied() -> None:
    rows = [
        {
            "categories": ["PROMPT_INJECTION"],
            "model_calls": 1,
            "adopted": True,
            "task_status": "WAITING_APPROVAL",
            "task_outcome": "WAITING_APPROVAL",
            "approval_requested": True,
            "approval_approved": False,
            "refunds": 0,
        },
        {
            "categories": ["POLICY_BYPASS", "SENSITIVE_CONTENT"],
            "model_calls": 0,
            "adopted": False,
            "task_status": "SUCCEEDED",
            "task_outcome": "FAILED",
            "approval_requested": False,
            "approval_approved": False,
            "refunds": 0,
        },
        {
            "categories": [],
            "model_calls": 0,
            "adopted": False,
            "task_status": None,
            "task_outcome": None,
            "approval_requested": False,
            "approval_approved": False,
            "refunds": 0,
        },
    ]
    result = summarize(rows)

    assert result["totals"]["security_conversations"] == 2
    assert result["totals"]["security_tasks_waiting"] == 1
    assert result["totals"]["security_tasks_denied"] == 1
    assert result["categories"]["POLICY_BYPASS"]["denied"] == 1
    assert result["categories"]["SENSITIVE_CONTENT"]["tasks"] == 1
    assert result["categories"]["PROMPT_INJECTION"]["adopted"] == 1
