"""Before/after evidence for clarification candidate downgrade (P11).

The claim this harness is built to support is narrow and checkable: *a customer
who cannot remember an order number used to reach a human on the second asking,
and now gets a chance to use their own order list before that happens.* The
number for that has to come from replaying the conversation, not from a
paragraph describing it.

Two budgets, the same scripted scenarios:

* ``budget=2`` reproduces the released behaviour. ``repeated_clarification``
  borrowed ``frustration_rounds`` (=2), so the second asking already *was* the
  handover.
* ``budget=3`` is the shipped rule: the second asking asks again and hands the
  caller their own recent orders, and only the third failing turn hands over.

One measurement note, because it is where the honest reading lives. Running the
old budget against today's code does NOT hide the candidate cards: the cards are
still attached to that turn, but the turn's reply is the handover wording and
the agent goes silent afterwards. So ``actionable_candidates`` counts candidate
cards **on a turn the agent was still serving** — a list the customer could
actually click. Under the old budget that number is zero for every scenario even
though cards exist, and the run prints the raw card count alongside it so the
difference between "shown" and "usable" stays visible.

Personas are deterministic. A ``cooperative`` caller sends the first order it is
handed on the turn where that still reaches the agent; a ``stuck`` caller keeps
failing to name an order. Nothing here calls a model, hits the network, or reads
the clock: the same run prints the same numbers.

Run it with::

    python evals/clarification_candidates.py
    python evals/clarification_candidates.py --json

These are scripted conversations over the seeded demo tenant, not production
telemetry. They measure what the threshold does to the handoff decision; they
say nothing about real traffic volume.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.seed import seed_all

try:
    from evals.run import _reset_state as _reset_demo_state
except ModuleNotFoundError:  # `python evals/clarification_candidates.py`
    from run import _reset_state as _reset_demo_state  # type: ignore[no-redef]

COOPERATIVE = "cooperative"
STUCK = "stuck"

OLD_BUDGET = 2
NEW_BUDGET = 3

# A conversation that needs more than this is a driver bug, not a scenario.
MAX_TURNS = 8

PERSONAS = (COOPERATIVE, STUCK)
SOURCES = ("authored", "external_phrasing")

SCENARIO_SOURCE = Path(__file__).with_name("clarification_scenarios.json")


@dataclass(frozen=True, slots=True)
class Scenario:
    """One scripted attempt to reach an order without naming it."""

    id: str
    persona: str
    opening: str
    followups: tuple[str, ...]
    source: str = "authored"
    provenance: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.persona not in PERSONAS:
            raise ValueError(f"Unknown persona: {self.persona}")
        if self.source not in SOURCES:
            raise ValueError(f"Unknown wording source: {self.source}")
        if self.source == "external_phrasing" and not self.provenance.startswith(
            "evals/"
        ):
            raise ValueError(f"{self.id}: external wording needs an evals/ provenance.")
        if self.persona == STUCK and len(self.followups) < 2:
            raise ValueError(f"{self.id}: a stuck caller needs two failing follow-ups.")


@dataclass(frozen=True, slots=True)
class RunResult:
    scenario_id: str
    persona: str
    budget: int
    handoff: bool
    handoff_signal: str | None
    actionable_candidates: int
    cards_on_handoff_turn: int
    resolved: bool
    picked_order_id: str | None
    turns: int


def load_scenarios(path: Path | None = None) -> list[Scenario]:
    """Read the scripted conversations from data, not code."""
    source = json.loads((path or SCENARIO_SOURCE).read_text(encoding="utf-8"))
    scenarios: list[Scenario] = []
    for row in source["scenarios"]:
        scenarios.append(
            Scenario(
                id=row["id"],
                persona=row["persona"],
                opening=row["opening"],
                followups=tuple(row["followups"]),
                source=row.get("source", "authored"),
                provenance=row.get("provenance", ""),
                note=row.get("note", ""),
            )
        )
    if not scenarios:
        raise ValueError("No scenarios in the clarification evidence set.")
    personas = {scenario.persona for scenario in scenarios}
    if personas != set(PERSONAS):
        raise ValueError(f"Scenario set must cover both personas, got {personas}.")
    return scenarios


SCENARIOS = load_scenarios()


def _headers(actor: dict[str, str]) -> dict[str, str]:
    return {
        "X-Tenant-Id": actor["tenant"],
        "X-User-Id": actor["user"],
        "X-Channel": "web",
    }


def _candidate_ids(body: dict[str, Any]) -> list[str]:
    return [
        str(card["card_id"])
        for card in body.get("context_cards") or []
        if card.get("kind") == "order_candidate"
    ]


def _handoff(body: dict[str, Any]) -> dict[str, Any] | None:
    """The handover that takes the conversation away, if this turn did."""

    payload = body.get("handoff") or {}
    if payload.get("status") == "HANDOFF_PENDING" and payload.get("takeover"):
        return payload
    return None


def _send(
    client: TestClient,
    headers: dict[str, str],
    conversation_id: str | None,
    message: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"message": message}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    response = client.post("/api/v1/support/messages", headers=headers, json=payload)
    response.raise_for_status()
    return response.json()


def run_scenario(
    client: TestClient,
    headers: dict[str, str],
    scenario: Scenario,
    *,
    budget: int,
) -> RunResult:
    """Play one conversation and record how it ended.

    The driver is deliberately dumb: it never guesses an order number. A
    cooperative caller spends its one pick on the first order it was actually
    handed; a stuck caller keeps saying it cannot remember until it runs out of
    things to say.
    """

    conversation_id: str | None = None
    turns = 0
    actionable = 0
    picked_order_id: str | None = None
    pending = list(scenario.followups)

    body = _send(client, headers, conversation_id, scenario.opening)
    conversation_id = body["conversation_id"]
    turns += 1

    for _ in range(MAX_TURNS):
        shown = _candidate_ids(body)
        handoff = _handoff(body)
        if handoff is not None:
            # The agent stopped serving here. Cards on this turn are decoration
            # for the seat, not a chance the customer got.
            return RunResult(
                scenario_id=scenario.id,
                persona=scenario.persona,
                budget=budget,
                handoff=True,
                handoff_signal=handoff.get("signal_id"),
                actionable_candidates=actionable,
                cards_on_handoff_turn=len(shown),
                resolved=False,
                picked_order_id=picked_order_id,
                turns=turns,
            )
        if shown:
            actionable = max(actionable, len(shown))

        message: str | None = None
        if shown and scenario.persona == COOPERATIVE and picked_order_id is None:
            # One click, one send: the chip does not retype anything either.
            picked_order_id = shown[0]
            message = picked_order_id
        elif pending:
            message = pending.pop(0)
        if message is None:
            break
        body = _send(client, headers, conversation_id, message)
        conversation_id = body["conversation_id"]
        turns += 1

    final_handoff = _handoff(body)
    final_cards = _candidate_ids(body)
    resolved = (
        final_handoff is None
        and body.get("clarification") is None
        and not body.get("agent_silent")
    )
    return RunResult(
        scenario_id=scenario.id,
        persona=scenario.persona,
        budget=budget,
        handoff=final_handoff is not None,
        handoff_signal=(final_handoff or {}).get("signal_id"),
        actionable_candidates=actionable,
        cards_on_handoff_turn=len(final_cards) if final_handoff else 0,
        resolved=resolved,
        picked_order_id=picked_order_id,
        turns=turns,
    )


def run_budget(
    client: TestClient,
    headers: dict[str, str],
    budget: int,
    *,
    scenarios: list[Scenario] | None = None,
) -> list[RunResult]:
    return [
        run_scenario(client, headers, scenario, budget=budget)
        for scenario in (scenarios or SCENARIOS)
    ]


def summarise(results: list[RunResult], *, budget: int) -> dict[str, Any]:
    """Rates first, counts second — a count alone cannot be compared."""

    total = len(results)
    if total == 0:
        raise ValueError("summarise() needs at least one result.")
    handoffs = sum(1 for result in results if result.handoff)
    resolved = sum(1 for result in results if result.resolved)
    with_candidates = sum(1 for result in results if result.actionable_candidates > 0)
    by_persona: dict[str, dict[str, int]] = {}
    for persona in PERSONAS:
        rows = [result for result in results if result.persona == persona]
        by_persona[persona] = {
            "scenarios": len(rows),
            "handoffs": sum(1 for result in rows if result.handoff),
            "resolved": sum(1 for result in rows if result.resolved),
        }
    return {
        "budget": budget,
        "scenarios": total,
        "handoff_rate": round(handoffs / total, 4),
        "self_resolve_rate": round(resolved / total, 4),
        "candidate_rate": round(with_candidates / total, 4),
        "mean_turns": round(sum(result.turns for result in results) / total, 2),
        "handoffs": handoffs,
        "resolved": resolved,
        "cards_on_handoff_turns": sum(result.cards_on_handoff_turn for result in results),
        "by_persona": by_persona,
    }


def _app(budget: int, database_url: str, *, reset: bool = False) -> FastAPI:
    application = create_app(
        Settings(
            environment="test",
            database_url=database_url,
            auto_create_tables=True,
            escalation_clarification_rounds=budget,
        )
    )
    if reset:
        # Seed after clearing: both budgets must see the same two demo orders.
        _reset_demo_state(application.state.session_factory, {"demo"})
    seed_all(application.state.session_factory)
    return application


def _fresh_url(database_url: str | None, tag: str) -> tuple[str, list[str], bool]:
    """A clean tenant per budget: shared state would make the second run a lie.

    With an explicit ``--database-url`` the same database is reused and the demo
    tenant's transactional rows are cleared between budgets, which is what
    ``evals/support_run.py --reset`` does for the same reason.
    """

    if database_url:
        return database_url, [], True
    directory = tempfile.mkdtemp(prefix=f"tp-clarify-{tag}-")
    return f"sqlite+pysqlite:///{Path(directory) / 'evidence.db'}", [directory], False


def report(
    database_url: str | None = None, *, scenarios: list[Scenario] | None = None
) -> dict[str, Any]:
    """Run both budgets against the same scenarios and diff them."""

    active = scenarios or SCENARIOS
    frames: dict[str, Any] = {}
    runs: dict[str, list[RunResult]] = {}
    cleanup: list[str] = []

    for label, budget in (("before", OLD_BUDGET), ("after", NEW_BUDGET)):
        url, created, shared = _fresh_url(database_url, label)
        cleanup.extend(created)
        application = _app(budget, url, reset=shared)
        client = TestClient(application)
        runs[label] = run_budget(
            client, _headers({"tenant": "demo", "user": "alice"}), budget,
            scenarios=active,
        )
        frames[label] = summarise(runs[label], budget=budget)
        application.state.engine.dispose()

    for directory in cleanup:
        shutil.rmtree(directory, ignore_errors=True)

    return {
        "before": frames["before"],
        "after": frames["after"],
        "delta": {
            "handoff_rate": round(
                frames["after"]["handoff_rate"] - frames["before"]["handoff_rate"], 4
            ),
            "self_resolve_rate": round(
                frames["after"]["self_resolve_rate"]
                - frames["before"]["self_resolve_rate"],
                4,
            ),
            "candidate_rate": round(
                frames["after"]["candidate_rate"] - frames["before"]["candidate_rate"], 4
            ),
            "mean_turns": round(
                frames["after"]["mean_turns"] - frames["before"]["mean_turns"], 2
            ),
        },
        "personas": list(PERSONAS),
        "scenarios": [asdict(scenario) for scenario in active],
        "runs": {
            label: [asdict(result) for result in results]
            for label, results in runs.items()
        },
    }


_ROWS = (
    ("handoff_rate", "转人工率"),
    ("self_resolve_rate", "自助解决率"),
    ("candidate_rate", "拿到可用候选的比例"),
    ("mean_turns", "平均轮次"),
)


def _print_table(data: dict[str, Any]) -> None:
    """A plain fixed-width table; the CJK label column is why widths are manual.

    Counting characters rather than display cells is deliberate: the label column
    would not line up in every terminal anyway, and guessing at East Asian width
    here would be more machinery than the four numbers are worth.
    """

    def row(label: str, before: Any, after: Any, delta: str) -> str:
        return f"{label:<24}{before!s:>12}{after!s:>12}{delta:>10}"

    print("澄清候选降级：同一批剧本，旧阈值 ESCALATION_CLARIFICATION_ROUNDS=2 / 新阈值=3\n")
    print(row("指标", "before(=2)", "after(=3)", "delta"))
    print("-" * 58)
    for key, label in _ROWS:
        before = data["before"][key]
        after = data["after"][key]
        print(row(label, before, after, f"{round(after - before, 2):+g}"))
    print(f"\n剧本数：{data['before']['scenarios']}（before / after 同一批）")
    print(
        "旧阈值下候选卡片仍会挂上转人工那一轮，共 "
        f"{data['before']['cards_on_handoff_turns']} 张，但客户已无法据此自助——"
        "所以「可用候选比例」为 0。"
    )
    for label in ("before", "after"):
        print(f"{label} 分 persona：")
        for persona, stats in data[label]["by_persona"].items():
            print(
                f"  {persona:<12} 转人工 {stats['handoffs']}/{stats['scenarios']}"
                f" · 自助解决 {stats['resolved']}/{stats['scenarios']}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure clarification candidate downgrade (before/after)."
    )
    parser.add_argument(
        "--database-url",
        help="Prefix for a named database instead of temporary SQLite files.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full report as JSON instead of the table.",
    )
    parser.add_argument(
        "--scenario-file",
        help="Override evals/clarification_scenarios.json.",
    )
    args = parser.parse_args(argv)

    scenarios = load_scenarios(Path(args.scenario_file)) if args.scenario_file else None
    data = report(args.database_url, scenarios=scenarios)
    if args.json:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    _print_table(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
