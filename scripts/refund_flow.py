"""Walk the write path against a running server: refund -> approval -> receipt.

``scripts/demo.py`` covers the read-only half. This covers the half that costs
money, and it goes through the product's own HTTP surface the way the console
does, with two different identities:

* the customer (``demo/alice``) asks for a refund in the chat endpoint;
* a staff seat (``demo/seat-01`` with ``X-Roles: approver``) sees the approval in
  the queue and decides it. A customer cannot approve their own refund, and the
  server enforces that regardless of what the headers claim.

Every step prints the wall time it took, because the point of the demo is that
the approval gate is a state machine, not a model decision: nothing executes
before ``APPROVE`` lands, and the receipt quotes the authoritative refund row.

The script is rerunnable. If the order already has an open approval it adopts
that one; if the refund already landed, it says so instead of pretending to work.

    python scripts/seed.py
    uvicorn app.asgi:app --port 8000
    python scripts/refund_flow.py
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

import httpx

TENANT = "demo"
CUSTOMER = "alice"
SEAT = "seat-01"


def timed(
    client: httpx.Client, method: str, path: str, **kwargs: Any
) -> tuple[float, httpx.Response]:
    started = time.perf_counter()
    response = client.request(method, path, **kwargs)
    return (time.perf_counter() - started) * 1000, response


def show(step: str, ms: float, status_code: int, line: str) -> None:
    print(f"{step}  {ms:>7.1f} ms  HTTP {status_code}")
    print(f"    {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--order", default="ORD-DEMO-1001", help="An order still refundable for this customer."
    )
    parser.add_argument("--amount", default="129", help="Yuan, as the customer would say it.")
    args = parser.parse_args()

    message = f"我要申请退款，订单 {args.order}，金额 {args.amount} 元"
    customer = httpx.Client(
        base_url=args.base_url,
        headers={"X-Tenant-Id": TENANT, "X-User-Id": CUSTOMER},
        timeout=60,
    )
    seat = httpx.Client(
        base_url=args.base_url,
        headers={"X-Tenant-Id": TENANT, "X-User-Id": SEAT, "X-Roles": "approver,support"},
        timeout=60,
    )

    with customer, seat:
        try:
            customer.get("/health/ready").raise_for_status()
        except httpx.HTTPError as exc:
            print(f"cannot reach {args.base_url}; start the server and seed first\n{exc}")
            return 2

        print(f"> 客户（{TENANT}/{CUSTOMER}）：{message}")
        ms, response = timed(
            customer, "POST", "/api/v1/support/messages", json={"message": message}
        )
        body = response.json()
        task = body.get("task") or {}
        task_status = task.get("status")
        show(
            "1. 提交退款请求",
            ms,
            response.status_code,
            f"intent={body.get('intent')}  task={task_status}",
        )
        print(f"    客服：{body.get('reply')}")

        task_id = task.get("task_id")
        approval_id = (task.get("pending_approval") or {}).get("approval_id")
        if task_status != "WAITING_APPROVAL":
            # Rerun, or a refusal that is itself the demo: the order already has
            # an approval, or the money already moved. The queue decides.
            ms, pending = timed(seat, "GET", "/api/v1/approvals", params={"status": "PENDING"})
            items = pending.json().get("items", [])
            if not items:
                show("2. 待审批队列", ms, pending.status_code, "0 条 —— 没有可审批的请求")
                print(
                    "    这一单已经退过，或资格校验就没通过（服务端拒绝重复退款）。\n"
                    "    换一个仍可退的订单重跑，或在控制台「审批工作台」看历史审批。"
                )
                return 0
            show("1b. 复用队列里已有的待审批项", ms, pending.status_code, json.dumps(
                {k: items[0].get(k) for k in ("approval_id", "task_id", "amount_cents", "status")},
                ensure_ascii=False,
            ))
            approval_id = items[0]["approval_id"]
            task_id = items[0]["task_id"]

        ms, queue = timed(seat, "GET", "/api/v1/approvals", params={"status": "PENDING"})
        items = queue.json().get("items", [])
        show(
            "2. 坐席看待审批队列",
            ms,
            queue.status_code,
            f"{len(items)} 条："
            + ", ".join(f"{item['approval_id']} {item.get('amount_cents')} 分" for item in items),
        )

        ms, decision = timed(
            seat,
            "POST",
            f"/api/v1/tasks/{task_id}/approve",
            json={
                "approval_id": approval_id,
                "decision": "APPROVE",
                "reason": "资格已核对，同意退款",
            },
        )
        after = decision.json()
        result = after.get("result") or {}
        show(
            "3. 坐席批准",
            ms,
            decision.status_code,
            f"task={after.get('status')}  outcome={result.get('outcome')}",
        )
        print(f"    回执：{result.get('summary')}")

        ms, events = timed(seat, "GET", f"/api/v1/tasks/{task_id}/events")
        timeline = [
            line.split(":", 1)[1].strip()
            for line in events.text.splitlines()
            if line.startswith("event:")
        ]
        show("4. 任务事件时间线", ms, events.status_code, " -> ".join(timeline) or "(空)")

        print("\n同一个订单再问一次：接管的是服务端的在途/已退校验，不是模型记不记得上一轮。")
        ms, repeat = timed(customer, "POST", "/api/v1/support/messages", json={"message": message})
        repeat_body = repeat.json()
        repeat_task = repeat_body.get("task") or {}
        repeat_result = repeat_task.get("result") or {}
        show(
            "5. 客户重复申请",
            ms,
            repeat.status_code,
            f"task={repeat_task.get('status')}  outcome={repeat_result.get('outcome')}",
        )
        print(f"    客服：{repeat_body.get('reply')}")
        print(
            "    台账："
            + json.dumps(
                {"summary": repeat_result.get("summary"), "checks": repeat_result.get("checks")},
                ensure_ascii=False,
            )[:400]
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
