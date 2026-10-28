"""The P11 evidence harness is measured, not narrated.

`evals/clarification_candidates.py` replays the same scripted conversations
against the old budget (2) and the new one (3) and reports rates. These tests
pin the two things that make that report worth reading: the driver actually
behaves like the persona it claims to run, and the aggregation reports rates
rather than raw counts.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from evals.clarification_candidates import (
    COOPERATIVE,
    NEW_BUDGET,
    OLD_BUDGET,
    SCENARIOS,
    SOURCES,
    STUCK,
    RunResult,
    load_scenarios,
    report,
    run_budget,
    summarise,
)

ALICE = {"tenant": "demo", "user": "alice"}


def _headers(actor: dict[str, str]) -> dict[str, str]:
    return {
        "X-Tenant-Id": actor["tenant"],
        "X-User-Id": actor["user"],
        "X-Channel": "web",
    }


def _client(app_factory: Callable[..., FastAPI], budget: int) -> TestClient:
    return TestClient(app_factory(escalation_clarification_rounds=budget))


# --------------------------------------------------------------------- 形状
def test_every_scenario_names_its_persona_and_its_wording_source() -> None:
    assert SCENARIOS
    assert {scenario.persona for scenario in SCENARIOS} == {COOPERATIVE, STUCK}
    for scenario in SCENARIOS:
        assert scenario.source in SOURCES
        assert scenario.opening.strip()
        assert scenario.followups


def test_external_wording_declares_where_it_came_from() -> None:
    external = [
        scenario for scenario in SCENARIOS if scenario.source == "external_phrasing"
    ]
    assert external, "the set should use real customer wording, not only authored lines"
    for scenario in external:
        assert scenario.provenance.startswith("evals/")


def test_a_stuck_scenario_is_long_enough_to_reach_the_third_asking() -> None:
    for scenario in SCENARIOS:
        if scenario.persona == STUCK:
            assert len(scenario.followups) >= 2


def test_the_scenario_file_is_the_only_source_of_scripts() -> None:
    assert load_scenarios() == SCENARIOS


# --------------------------------------------------------------------- 旧预算
def test_the_old_budget_left_the_customer_nothing_usable(
    app_factory: Callable[..., FastAPI],
) -> None:
    """Two askings was the rule, so the second asking *was* the handoff.

    The cards still ride along on that turn — that is the honest part of the
    number — but they are decoration for the seat, not something the customer
    could click, so the actionable count stays zero.
    """
    results = run_budget(_client(app_factory, OLD_BUDGET), _headers(ALICE), OLD_BUDGET)

    assert len(results) == len(SCENARIOS)
    for result in results:
        assert result.actionable_candidates == 0
        assert result.handoff is True
        assert result.resolved is False
    assert summarise(results, budget=OLD_BUDGET)["self_resolve_rate"] == 0.0
    assert summarise(results, budget=OLD_BUDGET)["candidate_rate"] == 0.0


# --------------------------------------------------------------------- 新预算
def test_a_cooperative_caller_finishes_without_a_human(
    app_factory: Callable[..., FastAPI],
) -> None:
    """The persona that picks a candidate must not reach a seat at all."""

    results = {
        result.scenario_id: result
        for result in run_budget(_client(app_factory, NEW_BUDGET), _headers(ALICE), NEW_BUDGET)
    }
    cooperative = [
        scenario for scenario in SCENARIOS if scenario.persona == COOPERATIVE
    ]
    assert cooperative
    for scenario in cooperative:
        result = results[scenario.id]
        assert result.actionable_candidates > 0
        assert result.handoff is False
        assert result.resolved is True
        assert result.picked_order_id in {"ORD-DEMO-1001", "ORD-DEMO-1002"}


def test_a_stuck_caller_still_reaches_a_human_by_the_same_signal(
    app_factory: Callable[..., FastAPI],
) -> None:
    """Downgrading is not refusing: the third failing turn still hands over."""

    results = {
        result.scenario_id: result
        for result in run_budget(_client(app_factory, NEW_BUDGET), _headers(ALICE), NEW_BUDGET)
    }
    stuck = [scenario for scenario in SCENARIOS if scenario.persona == STUCK]
    assert stuck
    for scenario in stuck:
        result = results[scenario.id]
        assert result.handoff is True
        assert result.handoff_signal == "repeated_clarification"
        assert result.actionable_candidates > 0
        assert result.resolved is False


# --------------------------------------------------------------------- 汇总
def test_the_report_aggregates_rates_and_keeps_the_two_personas_apart() -> None:
    results = [
        RunResult("a", COOPERATIVE, 3, False, None, 2, 0, True, "ORD-1", 2),
        RunResult("b", STUCK, 3, True, "repeated_clarification", 0, 2, False, None, 3),
    ]

    frame = summarise(results, budget=3)

    assert frame["budget"] == 3
    assert frame["scenarios"] == 2
    assert frame["handoff_rate"] == 0.5
    assert frame["self_resolve_rate"] == 0.5
    assert frame["candidate_rate"] == 0.5
    assert frame["by_persona"][COOPERATIVE]["resolved"] == 1
    assert frame["by_persona"][STUCK]["handoffs"] == 1
    # Rates, not counts: a report that only summed numbers could not be compared.
    assert 0.0 <= frame["handoff_rate"] <= 1.0


def test_summarise_refuses_an_empty_run() -> None:
    try:
        summarise([], budget=3)
    except ValueError as error:
        assert "at least one" in str(error)
    else:
        raise AssertionError("summarise() must not report rates for zero runs.")


def test_the_two_budgets_actually_differ_on_the_same_scenarios(
    app_factory: Callable[..., FastAPI],
) -> None:
    """The whole point of the change, as one assertion."""

    old = summarise(
        run_budget(_client(app_factory, OLD_BUDGET), _headers(ALICE), OLD_BUDGET),
        budget=OLD_BUDGET,
    )
    new = summarise(
        run_budget(_client(app_factory, NEW_BUDGET), _headers(ALICE), NEW_BUDGET),
        budget=NEW_BUDGET,
    )

    # Same scripts, same tenant, same seeded orders — only the budget differs.
    assert old["scenarios"] == new["scenarios"] == len(SCENARIOS)
    assert new["self_resolve_rate"] > old["self_resolve_rate"]
    assert new["handoff_rate"] < old["handoff_rate"]
    assert new["candidate_rate"] > old["candidate_rate"]


def test_the_published_report_shape_is_stable() -> None:
    data = report()

    assert set(data) == {"before", "after", "delta", "personas", "scenarios", "runs"}
    assert data["delta"]["handoff_rate"] < 0
    assert data["delta"]["self_resolve_rate"] > 0
    assert len(data["runs"]["before"]) == len(data["runs"]["after"]) == len(SCENARIOS)
