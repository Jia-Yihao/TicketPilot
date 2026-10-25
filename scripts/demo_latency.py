"""Measure real turn latency over HTTP, by turn kind, against a running server.

The business-metrics runner (`evals/business_metrics.py`) measures turns through
`TestClient` in one process. That is the right place to measure the product's
own rates, but it hides two things a caller would notice: the HTTP stack and the
serialisation. This script measures what a browser or a channel adapter
actually waits for, on the same endpoints, and splits the answer by turn kind
because the kinds are not equally expensive:

* read-only turns (product, policy, order, logistics, self-service statistics)
  never touch the task ledger and never call a model;
* a write turn (refund request) additionally creates a task, runs the eligibility
  check, and writes approvals + ledger rows — and in ``AGENT_MODE=pydantic_ai``
  it is also the only kind that asks the model anything.

Read-only kinds are the default because they can be repeated; a refund request
against the same order is intentionally refused after the first one ("this
resource already has an open approval"), so ``--with-refund`` runs exactly once
and prints what it got back.

    python scripts/demo_latency.py --repeat 10
    python scripts/demo_latency.py --base-url http://127.0.0.1:8000 --with-refund
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import httpx

READ_TURNS: tuple[tuple[str, str], ...] = (
    ("商品", "订单 ORD-DEMO-1001 买的是什么商品？桌板材质和承重是多少？"),
    ("政策", "退款条件是什么？已签收多久内可以申请？"),
    ("订单", "查一下订单 ORD-DEMO-1001 的状态"),
    ("物流", "ORD-DEMO-1002 的物流到哪了？"),
    ("统计", "我今年退了多少款"),
)
REFUND_TURN = ("退款（写路径）", "我要申请退款，订单 ORD-DEMO-1001，金额 129 元")


def percentile(values: list[float], share: float) -> float:
    """Nearest-rank percentile; the sample sizes here are far too small for more."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


def one_turn(
    client: httpx.Client, message: str, *, conversation_id: str | None = None
) -> tuple[float, dict[str, Any]]:
    payload: dict[str, Any] = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    started = time.perf_counter()
    response = client.post("/api/v1/support/messages", json=payload)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return elapsed_ms, response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--tenant", default="demo")
    parser.add_argument("--user", default="alice")
    parser.add_argument("--repeat", type=int, default=10, help="Read-only turns per kind.")
    parser.add_argument(
        "--with-refund",
        action="store_true",
        help="Also run one refund request (creates a task and a pending approval).",
    )
    args = parser.parse_args()

    headers = {"X-Tenant-Id": args.tenant, "X-User-Id": args.user}
    with httpx.Client(
        base_url=args.base_url, headers=headers, timeout=60
    ) as client:
        try:
            client.get("/health/ready").raise_for_status()
        except httpx.HTTPError as exc:
            print(
                f"cannot reach {args.base_url} — start the server and run scripts/seed.py "
                f"first\n{exc}",
                file=sys.stderr,
            )
            return 2

        print(f"target={args.base_url}  identity={args.tenant}/{args.user}  repeat={args.repeat}")
        print(f"{'kind':12} {'n':>4} {'p50 ms':>9} {'p95 ms':>9} {'max ms':>9}  intent")
        turns: list[tuple[str, str, int]] = [
            (kind, message, args.repeat) for kind, message in READ_TURNS
        ]
        if args.with_refund:
            # Once only: the second request for the same order is refused as an
            # in-flight duplicate, which is the behaviour, not a bug.
            turns.append((REFUND_TURN[0], REFUND_TURN[1], 1))
        samples: dict[str, list[float]] = {}
        intents: dict[str, str] = {}
        for kind, message, repeats in turns:
            values: list[float] = []
            for _ in range(repeats):
                elapsed, body = one_turn(client, message)
                values.append(elapsed)
                intents[kind] = str(body.get("intent"))
            samples[kind] = values
            print(
                f"{kind:12} {len(values):>4} "
                f"{percentile(values, 0.5):>9.1f} {percentile(values, 0.95):>9.1f} "
                f"{max(values):>9.1f}  {intents[kind]}"
            )

        every = [value for values in samples.values() for value in values]
        print(
            f"{'合计':12} {len(every):>4} "
            f"{percentile(every, 0.5):>9.1f} {percentile(every, 0.95):>9.1f} "
            f"{max(every):>9.1f}"
        )
        print(
            "口径：真实 HTTP + uvicorn 单进程，SQLite；每条问句独立会话，避免上下文继承改变工作量；"
            "延迟含网络回环与序列化，不含客户端渲染。"
        )
        if args.with_refund:
            print("注意：--with-refund 会留下一个 WAITING_APPROVAL 任务，在控制台审批工作台处理。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
