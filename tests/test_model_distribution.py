from __future__ import annotations

from typing import Any

from app.agent.pydantic_agent import IntentLiteral, IntentOutput, ModelTrace
from evals.model_distribution import CASES, Case, _tv_distance, run


class _StubExtractor:
    def __init__(self, outputs: dict[str, IntentOutput]) -> None:
        self.outputs = outputs

    def parse(
        self, request: str, context: dict[str, Any] | None = None
    ) -> tuple[IntentOutput, ModelTrace]:
        return self.outputs[request], ModelTrace(requests=1)


def _output(
    intent: IntentLiteral, *, amount_cents: int | None = None, confidence: float = 0.8
) -> IntentOutput:
    return IntentOutput(
        intent=intent,
        amount_cents=amount_cents,
        confidence=confidence,
        decision_summary="stub",
    )


def test_distribution_counts_adoption_amount_ranges_and_controls() -> None:
    gap = Case(
        "refund-partial",
        "我申请退一部分款",
        "REFUND_REQUEST",
        amount_range=(12_000, 14_000),
    )
    control = CASES[11]  # rule-readable refund; the model disagrees
    cases = (gap, control)
    extractor = _StubExtractor(
        {
            gap.text: _output("REFUND_REQUEST", amount_cents=12_900),
            control.text: _output("TICKET_CREATE"),
        }
    )

    snapshot = run(cases, extractor, repeat=2, model="stub")

    totals = snapshot["totals"]
    assert totals["turns"] == 4
    assert totals["rules_unknown_turns"] == 2
    assert totals["adopted_changes"] == 2
    assert totals["adoption_change_rate"] == 1.0
    # The control is readable by the rules, so the disagreeing model changes
    # neither its intent nor its amount.
    control_row = next(row for row in snapshot["cases"] if row["id"] == control.id)
    assert control_row["adopted_intents"] == {"REFUND_REQUEST": 2}
    assert control_row["amounts"] == {"within": 2}
    # Both cases have an amount range; the gap's 12900 is inside 12000-14000
    # and the control's rule amount is 12900 exactly.
    assert totals["amount_turns"] == 4
    assert totals["amount_within"] == 4
    assert totals["amount_accuracy"] == 1.0
    assert totals["order_fabricated"] == 0
    assert totals["raw_label_accuracy"] == 0.5


def test_distribution_separates_missing_and_out_of_range_amounts() -> None:
    case = Case(
        "amount", "我申请退一部分款", "REFUND_REQUEST", amount_range=(9_000, 11_000)
    )
    missing = _StubExtractor({case.text: _output("REFUND_REQUEST")})
    outside = _StubExtractor(
        {case.text: _output("REFUND_REQUEST", amount_cents=50_000)}
    )

    missing_run = run((case,), missing, repeat=1, model="stub")
    outside_run = run((case,), outside, repeat=1, model="stub")

    assert missing_run["cases"][0]["amounts"] == {"missing": 1}
    assert outside_run["cases"][0]["amounts"] == {"outside": 1}
    assert missing_run["totals"]["amount_accuracy"] == 0.0
    assert outside_run["totals"]["amount_accuracy"] == 0.0


def test_tv_distance_is_zero_for_the_same_distribution() -> None:
    assert _tv_distance({"A": 3, "B": 1}, {"A": 3, "B": 1}) == 0.0
    assert _tv_distance({"A": 2}, {"B": 2}) == 1.0
