"""Does the model change any decision? Measured on the simulated traffic.

`IntentBrain` (``app/agent/pydantic_agent.py``) is deliberately not a router. The
rules run first, and the model is only allowed to *fill what the rules left
empty*: its intent is adopted when the rules returned ``UNKNOWN``, and its
``order_id`` / ``amount_cents`` only when the rules did not extract one. So
"AGENT_MODE=pydantic_ai" does not mean "the model decides"; it means "the model
gets a vote on the gaps".

This script answers the question an interviewer will actually ask — how many
turns did that change? — by replaying every write-path turn of
``evals/business_metrics.py``'s scenario set through both paths and printing the
field-level diff, together with the counters of what the model path cost (calls,
failures, tokens, wall time).

Only write turns are replayed, because only the task runtime calls the model;
read turns (policy, product, order, logistics, statistics) never reach it.

"Write turn" here means a turn that a *context-free* ``classify()`` reads as
``REFUND_REQUEST`` / ``TICKET_CREATE`` — 16 of them in the current scenario set.
The live traffic only routes 12: four 「别绕了，转人工吧」 turns land in
conversations a human has already taken over
(``app/agent/support_agent.py:181-192`` returns before any task is created), so
they never reach the runtime. This script still asks the model about them, which
is four extra calls inside the cost it reports — a price the real traffic does
not pay.

An empty diff is the honest outcome on this traffic and should be read as such:
the simulated queue was written from the same rule vocabulary the classifier
uses, so there is no phrasing here that the rules could not parse. It is
evidence that the model cannot *break* anything on this traffic, not evidence
that it adds value. Proving the second needs real tickets whose wording the
rules miss.

    OPENAI_BASE_URL=http://127.0.0.1:1234/v1 OPENAI_API_KEY=lmstudio \\
      python scripts/llm_boundary_diff.py --model openai:qwen/qwen3-8b
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.agent.pydantic_agent import IntentAgent, IntentBrain
from app.agent.routing import RoutePlan, classify

try:
    from evals.business_metrics import SCENARIOS
except ModuleNotFoundError:  # editable install maps ``app`` only, not ``evals``
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from evals.business_metrics import SCENARIOS

# The four fields a route plan carries, and the only surface the model can touch.
FIELDS = ("intent", "order_id", "amount_cents", "amount_invalid")


def write_turns() -> list[str]:
    """The turns that would create a task, straight from the scenario script."""
    turns: list[str] = []
    for scenario in SCENARIOS:
        for text in scenario.turns:
            if classify(text, {}).intent.value in ("REFUND_REQUEST", "TICKET_CREATE"):
                turns.append(text)
    return turns


def diff(rules: RoutePlan, merged: RoutePlan) -> dict[str, tuple[object, object]]:
    return {
        field: (getattr(rules, field), getattr(merged, field))
        for field in FIELDS
        if getattr(rules, field) != getattr(merged, field)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model",
        default="openai:qwen/qwen3-8b",
        help="pydantic-ai model string; OpenAI-compatible servers use OPENAI_BASE_URL.",
    )
    args = parser.parse_args()

    turns = write_turns()
    print(f"write-path turns in the simulated traffic: {len(turns)}")
    print("（只读轮不列出：它们根本不经过模型。）\n")

    agent = IntentAgent(model_name=args.model)
    brain = IntentBrain(mode="pydantic_ai", agent=agent)

    changed = 0
    sources: dict[str, int] = {}
    for text in turns:
        rules = classify(text, {})
        merged, source = brain.plan(text, {})
        sources[source] = sources.get(source, 0) + 1
        delta = diff(rules, merged)
        if delta:
            changed += 1
            print(f"CHANGED  {text[:40]}")
            for field, (before, after) in delta.items():
                print(f"    {field}: {before!r} -> {after!r}  ({source})")
        else:
            print(
                f"same     {text[:40]}  intent={merged.intent.value} "
                f"order_id={merged.order_id} amount_cents={merged.amount_cents} ({source})"
            )

    usage = agent.usage.as_dict()
    print(f"\nmodel changed the plan on {changed}/{len(turns)} turns; routing sources={sources}")
    print(
        f"model cost: calls={usage['calls']} failures={usage['failures']} "
        f"requests={usage['model_requests']} "
        f"tokens={usage['input_tokens']}+{usage['output_tokens']} "
        f"wall={usage['latency_ms']:.0f} ms avg={usage['avg_latency_ms']:.0f} ms "
        f"avg_tokens={usage['avg_total_tokens']}"
    )
    print(
        "口径：同一进程内重放，规则路径先行；计数器来自 IntentAgent.parse()，"
        "失败的那次也计入 calls 与耗时；换模型或流量这两个数字都会变。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
