"""Where the model can still buy a decision: the phrasings the rules cannot read.

``scripts/llm_boundary_diff.py`` asks "does the model change any of my simulated
traffic" and the answer is 0/16. That number is only half a story — it says the
rule table already read every turn I wrote, and every turn I wrote was drawn
from the same vocabulary that rule table uses.

This script goes the other way. It starts from sentences that
``app/agent/routing.py`` reads as ``UNKNOWN`` — the only condition under which
``IntentBrain`` (``app/agent/pydantic_agent.py:163-165``) is allowed to adopt the
model's intent — and asks whether the model recovers the reading a human would
take. Slot fills are counted separately: the model may also supply an
``order_id`` / ``amount_cents`` the rules did not extract.

Two boundaries to keep in view while reading the output:

* **the labels are mine.** These are hand-written phrasings that deliberately
  avoid the rule vocabulary, not real tickets from a tenant's queue. A non-zero
  row proves the gap exists in principle, not that it is common;
* **the chat path now asks the model too, but only for a refund.** Since
  ``SupportAgent._consult_model`` (``app/agent/support_agent.py``) an
  ``UNKNOWN`` turn (or one the catalog only matched through the order-id
  fallback) is passed to ``IntentBrain`` under ``AGENT_MODE=pydantic_ai``, and
  only a ``REFUND_REQUEST`` reading is adopted — the write action that still
  waits for eligibility and a human approval. ``--chat`` shows the deterministic
  answer, ``--chat-model`` the same turns with the gate on, ``--chat-task`` the
  end-to-end refund (slots filled, task in ``WAITING_APPROVAL``), and
  ``--live-task`` the task API, where the model was always reachable. With
  ``AGENT_MODE=rules`` nothing calls a model.

The fabrication check is not decoration: ``INSTRUCTIONS`` tells the extractor to
copy an order id verbatim and never invent one, so an order id in the merged plan
that is not a substring of the message is a violation of that rule — a refund
aimed at an order the customer never mentioned. The script exits non-zero if that
ever happens.

    OPENAI_BASE_URL=http://127.0.0.1:1234/v1 OPENAI_API_KEY=lmstudio \\
      python scripts/llm_gap_probe.py --chat --chat-model \\
      --chat-task "ORD-DEMO-1001 那笔钱给我退回来"

    OPENAI_BASE_URL=http://127.0.0.1:1234/v1 OPENAI_API_KEY=lmstudio \\
      python scripts/llm_gap_probe.py --live-task "ORD-DEMO-1001 退一百二十九元"

    python scripts/llm_gap_probe.py --no-model --chat   # the deterministic half
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent.pydantic_agent import IntentAgent, IntentBrain
from app.agent.routing import classify
from app.core.config import Settings
from app.core.security import Identity
from app.main import create_app
from app.models.task import Task, TaskEvent
from app.seed import seed_all


@dataclass(frozen=True, slots=True)
class Case:
    """A message, plus the reading a human would take from it."""

    text: str
    intent: str
    order_id: str | None = None
    amount_cents: int | None = None


# Sentences that share one property: none of them contains a token from the rule
# tables in ``app/agent/routing.py`` that would let ``classify()`` commit to an
# intent. The labels are the author's reading, and are the only ground truth here.
CANDIDATES: tuple[Case, ...] = (
    Case("ORD-DEMO-1001 那笔钱给我退回来", "REFUND_REQUEST", order_id="ORD-DEMO-1001"),
    Case("我上周买的那个杯子收到就碎了，这单我不要了，把钱还我", "REFUND_REQUEST"),
    Case("我申请退一部分款", "REFUND_REQUEST"),
    Case("东西根本没法用，我不想留了，把付的钱给我回来", "REFUND_REQUEST"),
    Case(
        "订单 ORD-DEMO-1001，金额一百二十九元，我要申请退款", "REFUND_REQUEST", amount_cents=12900
    ),
    Case(
        "ORD-DEMO-1001 退一百二十九元",
        "REFUND_REQUEST",
        order_id="ORD-DEMO-1001",
        amount_cents=12900,
    ),
    Case("别跟我绕了，叫个活人来", "TICKET_CREATE"),
    Case("我要找你们负责人，机器人在糊弄我", "TICKET_CREATE"),
    Case("这事儿我忍了三次了，给我找个真人", "TICKET_CREATE"),
    Case("运费谁出？", "POLICY_QA"),
    Case("我买的东西还没到，能等到几号", "LOGISTICS_QUERY"),
)


@dataclass(slots=True)
class Row:
    text: str
    label: str
    rules_intent: str
    merged_intent: str
    source: str
    rules_amount: int | None
    merged_amount: int | None
    intent_gap: bool
    recovered: bool
    amount_gap: bool
    amount_filled: bool
    invented_order_id: str | None

    @property
    def order(self) -> str:
        if self.intent_gap:
            return "RECOVERED" if self.recovered else "MISSED"
        return "SLOT" if self.amount_gap else "ALREADY-READ"


def probe(case: Case, brain: IntentBrain) -> Row:
    rules = classify(case.text, {})
    merged, source, _trace = brain.plan(case.text, {})
    intent_gap = rules.intent.value == "UNKNOWN"
    amount_gap = rules.amount_cents is None and case.amount_cents is not None
    invented = (
        merged.order_id
        if merged.order_id is not None and merged.order_id.lower() not in case.text.lower()
        else None
    )
    return Row(
        text=case.text,
        label=case.intent,
        rules_intent=rules.intent.value,
        merged_intent=merged.intent.value,
        source=source,
        rules_amount=rules.amount_cents,
        merged_amount=merged.amount_cents,
        intent_gap=intent_gap,
        recovered=intent_gap and merged.intent.value == case.intent,
        amount_gap=amount_gap,
        amount_filled=amount_gap and merged.amount_cents == case.amount_cents,
        invented_order_id=invented,
    )


def run_probe(
    model: str, cases: tuple[Case, ...], as_json: bool
) -> tuple[list[Row], dict[str, Any]]:
    agent = IntentAgent(model_name=model)
    brain = IntentBrain(mode="pydantic_ai", agent=agent)
    rows = [probe(case, brain) for case in cases]
    usage = agent.usage.as_dict()
    if as_json:
        print(
            json.dumps(
                {"rows": [asdict(row) for row in rows], "usage": usage},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for row in rows:
            flags = f"intent {row.rules_intent} -> {row.merged_intent} ({row.source})"
            if row.rules_amount != row.merged_amount:
                flags += f" amount {row.rules_amount} -> {row.merged_amount}"
            if row.invented_order_id:
                flags += f" INVENTED order_id={row.invented_order_id}"
            print(f"{row.order:13} label={row.label:16} {row.text[:34]:38} {flags}")

    intent_gaps = [row for row in rows if row.intent_gap]
    amount_gaps = [row for row in rows if row.amount_gap]
    invented = [row for row in rows if row.invented_order_id]
    summary = {
        "candidates": len(rows),
        "rules_read_as_unknown": len(intent_gaps),
        "model_recovered_label": sum(1 for row in intent_gaps if row.recovered),
        "amount_gaps": len(amount_gaps),
        "amounts_filled_correctly": sum(1 for row in amount_gaps if row.amount_filled),
        "invented_order_ids": len(invented),
        "usage": usage,
    }
    if not as_json:
        print(
            f"\nrules could not read {len(intent_gaps)}/{len(rows)} of these sentences; "
            f"the model landed on the labelled intent for "
            f"{sum(1 for row in intent_gaps if row.recovered)} of them."
        )
        print(
            f"amount gaps: {len(amount_gaps)}, filled correctly: "
            f"{sum(1 for row in amount_gaps if row.amount_filled)}; "
            f"order ids invented: {len(invented)}"
        )
        print(
            f"model cost: calls={usage['calls']} failures={usage['failures']} "
            f"tokens={usage['input_tokens']}+{usage['output_tokens']} "
            f"wall={usage['latency_ms']:.0f} ms avg={usage['avg_latency_ms']:.0f} ms"
        )
        print(
            "口径：规则先行，模型只被允许在规则给 UNKNOWN 时定意图、在规则没抽到时补槽位；"
            "标签是我按人读法写的，不是真实工单。"
        )
    return rows, summary


def build_settings(database_url: str, *, agent_mode: str = "rules") -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        auto_create_tables=True,
        agent_mode=agent_mode,
    )


def live_task(text: str, model: str, tenant_id: str, user_id: str) -> dict[str, Any]:
    """Run one request through ``POST /api/v1/tasks``' own runtime, rules vs model.

    The chat path never gets here for an ``UNKNOWN`` message, so the comparison is
    made at the task API: same seeded database, same identity, two brains.
    """
    outcomes: dict[str, Any] = {}
    for mode in ("rules", "pydantic_ai"):
        scratch = Path(tempfile.gettempdir()) / f"ticketpilot-gap-{uuid.uuid4().hex}.db"
        app = create_app(
            build_settings(f"sqlite+pysqlite:///{scratch.as_posix()}"),
            brain=(
                None
                if mode == "rules"
                else IntentBrain(mode="pydantic_ai", agent=IntentAgent(model_name=model))
            ),
        )
        seed_all(app.state.session_factory)
        try:
            task = app.state.runtime.create_task(Identity(tenant_id, user_id, frozenset()), text)
            with app.state.session_factory() as session:
                events = session.execute(
                    select(TaskEvent.event_type, TaskEvent.payload)
                    .where(TaskEvent.task_id == task["task_id"])
                    .order_by(TaskEvent.created_at)
                ).all()
            routed = next((payload for name, payload in events if name == "task.routed"), {})
            result = task.get("result") or {}
            outcomes[mode] = {
                "status": task["status"],
                "routed_intent": routed.get("intent"),
                "planning_source": routed.get("planning_source"),
                "outcome": result.get("outcome"),
                "summary": result.get("summary"),
                "pending_approval": (task.get("pending_approval") or {}).get("amount_cents")
                if task.get("pending_approval")
                else None,
                "events": [name for name, _ in events],
            }
        finally:
            dispose(app, scratch)
    return outcomes


def dispose(app: Any, scratch: Path) -> None:
    app.state.engine.dispose()
    try:
        scratch.unlink()
    except OSError:
        print(f"（临时库留在 {scratch}，SQLite 还占着句柄）")


def run_chat(
    cases: tuple[Case, ...],
    tenant_id: str,
    user_id: str,
    as_json: bool,
    *,
    model: str | None = None,
) -> dict[str, Any]:
    """What the customer actually got: the same sentences through the chat path.

    With ``model=None`` the app runs ``AGENT_MODE=rules``; with a model string it
    runs ``AGENT_MODE=pydantic_ai``, where ``SupportAgent._consult_model`` asks
    the model on a turn the catalog cannot read. Either way this is a single
    turn per sentence: a refund that gets a clarification is not followed up,
    so the task count stays 0 unless a sentence already carried both slots.
    ``chat_task`` runs the follow-ups.
    """
    scratch = Path(tempfile.gettempdir()) / f"ticketpilot-gap-chat-{uuid.uuid4().hex}.db"
    agent = IntentAgent(model_name=model) if model else None
    app = create_app(
        build_settings(
            f"sqlite+pysqlite:///{scratch.as_posix()}",
            agent_mode="pydantic_ai" if agent else "rules",
        ),
        brain=IntentBrain(mode="pydantic_ai", agent=agent) if agent else None,
    )
    seed_all(app.state.session_factory)
    client = TestClient(app)
    headers = {"X-Tenant-Id": tenant_id, "X-User-Id": user_id}
    mode = "pydantic_ai" if agent else "rules"
    rows: list[dict[str, Any]] = []
    try:
        for case in cases:
            body = {"message": case.text, "conversation_id": f"gap-{uuid.uuid4().hex[:12]}"}
            response = client.post("/api/v1/support/messages", headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            rows.append(
                {
                    "text": case.text,
                    "label": case.intent,
                    "chat_intent": data.get("intent"),
                    "handoff": bool(data.get("handoff")),
                    "clarification": (data.get("clarification") or {}).get("expected_slot"),
                    "task_status": (data.get("task") or {}).get("status"),
                    "reply": (data.get("reply") or "").strip(),
                }
            )
        with app.state.session_factory() as session:
            tasks = int(session.scalar(select(func.count()).select_from(Task)) or 0)
    finally:
        dispose(app, scratch)
    usage = agent.usage.as_dict() if agent else None
    if as_json:
        print(
            json.dumps(
                {"mode": mode, "chat": rows, "tasks_created": tasks, "usage": usage},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"\n同样这些句子走对话入口（POST /api/v1/support/messages，AGENT_MODE={mode}）：")
        for row in rows:
            mark = (
                "handoff"
                if row["handoff"]
                else (
                    f"clarify:{row['clarification']}"
                    if row["clarification"]
                    else (row["task_status"] or "answer")
                )
            )
            print(
                f"  label={row['label']:16} chat={row['chat_intent']:16} {mark:22} "
                f"{row['text'][:26]:30} | {row['reply'][:42]}"
            )
        print(f"\n这些对话轮产生的写动作（任务/审批）：{tasks} / {len(rows)} 轮")
        if usage:
            print(
                f"模型代价：calls={usage['calls']} failures={usage['failures']} "
                f"tokens={usage['input_tokens']}+{usage['output_tokens']} "
                f"wall={usage['latency_ms']:.0f} ms avg={usage['avg_latency_ms']:.0f} ms"
            )
    return {"mode": mode, "rows": rows, "tasks_created": tasks, "usage": usage}


def chat_task(text: str, model: str, tenant_id: str, user_id: str) -> dict[str, Any]:
    """One sentence through the chat path, filling the slots it asks for.

    This is the end-to-end claim: the model rescues the reading in
    ``_consult_model``, the catalog asks for the slot it still lacks, and the
    refund that follows lands in the ledger as ``WAITING_APPROVAL`` with
    ``planning_source=pydantic_ai``.
    """
    scratch = Path(tempfile.gettempdir()) / f"ticketpilot-gap-chat-{uuid.uuid4().hex}.db"
    agent = IntentAgent(model_name=model)
    app = create_app(
        build_settings(f"sqlite+pysqlite:///{scratch.as_posix()}", agent_mode="pydantic_ai"),
        brain=IntentBrain(mode="pydantic_ai", agent=agent),
    )
    seed_all(app.state.session_factory)
    client = TestClient(app)
    headers = {"X-Tenant-Id": tenant_id, "X-User-Id": user_id}
    steps: list[dict[str, Any]] = []
    conversation: str | None = None
    message = text
    task: dict[str, Any] | None = None
    try:
        for _ in range(3):
            response = client.post(
                "/api/v1/support/messages",
                headers=headers,
                json={"message": message, "conversation_id": conversation},
            )
            response.raise_for_status()
            data = response.json()
            conversation = data["conversation_id"]
            clarification = data.get("clarification") or {}
            task = data.get("task")
            steps.append(
                {
                    "message": message,
                    "intent": data.get("intent"),
                    "reply": (data.get("reply") or "").strip(),
                    "expected_slot": clarification.get("expected_slot"),
                    "task_status": (task or {}).get("status"),
                }
            )
            if task:
                break
            slot = clarification.get("expected_slot")
            if slot == "amount_cents":
                message = "129 元"
            elif slot == "order_id":
                message = "ORD-DEMO-1001"
            else:
                break
        routed: dict[str, Any] = {}
        events: list[str] = []
        if task:
            with app.state.session_factory() as session:
                rows = session.execute(
                    select(TaskEvent.event_type, TaskEvent.payload)
                    .where(TaskEvent.task_id == task["task_id"])
                    .order_by(TaskEvent.created_at)
                ).all()
            events = [name for name, _ in rows]
            routed = next((dict(payload) for name, payload in rows if name == "task.routed"), {})
        usage = agent.usage.as_dict()
    finally:
        dispose(app, scratch)
    return {
        "text": text,
        "steps": steps,
        "task": task,
        "routed": routed,
        "events": events,
        "usage": usage,
    }


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        # The candidates are Chinese; a cp936 console would otherwise mojibake them.
        sys.stdout.reconfigure(encoding="utf-8")
    # The probe's output is the evidence; per-request httpx INFO lines are noise.
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model",
        default="openai:qwen/qwen3-8b",
        help="pydantic-ai model string; OpenAI-compatible servers use OPENAI_BASE_URL.",
    )
    parser.add_argument(
        "--live-task",
        metavar="TEXT",
        help="Also run this one request through the task runtime under both brains.",
    )
    parser.add_argument("--tenant", default="demo", help="Identity for --live-task/--chat.")
    parser.add_argument("--user", default="alice", help="Identity for --live-task/--chat.")
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Send every candidate through the chat endpoint under AGENT_MODE=rules.",
    )
    parser.add_argument(
        "--chat-model",
        action="store_true",
        help="Same chat pass under AGENT_MODE=pydantic_ai (one model call per unreadable turn).",
    )
    parser.add_argument(
        "--chat-task",
        metavar="TEXT",
        help="Chat E2E: one sentence, fill the slots it asks for, print the task ledger.",
    )
    parser.add_argument(
        "--no-model",
        action="store_true",
        help="Skip the direct brain pass; --chat-model/--chat-task still call the model.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table.")
    args = parser.parse_args()

    summary: dict[str, Any] = {"invented_order_ids": 0}
    rows: list[Row] = []
    if not args.no_model:
        if not args.json:
            print(f"规则读不懂的句式，模型能不能捞回来（模型：{args.model}）\n")
        rows, summary = run_probe(args.model, CANDIDATES, as_json=bool(args.json))

    passes: list[dict[str, Any]] = []
    if args.chat:
        passes.append(run_chat(CANDIDATES, args.tenant, args.user, as_json=bool(args.json)))
    if args.chat_model:
        passes.append(
            run_chat(
                CANDIDATES,
                args.tenant,
                args.user,
                as_json=bool(args.json),
                model=args.model,
            )
        )
    if len(passes) == 2 and not args.json:
        before, after = passes
        print("\n对话入口前后对照（同样的句子，AGENT_MODE=rules -> pydantic_ai）：")
        changed = 0
        for old, new in zip(before["rows"], after["rows"], strict=True):
            marker = ""
            if old["chat_intent"] != new["chat_intent"]:
                changed += 1
                marker = "  <- 变了"
            print(f"  {new['label']:16} {old['chat_intent']:16} -> {new['chat_intent']:16}{marker}")
        print(
            f"  意图改变：{changed}/{len(after['rows'])} 句；"
            f"单轮写动作：rules {before['tasks_created']} 个，"
            f"pydantic_ai {after['tasks_created']} 个"
        )

    if args.chat_task:
        flow = chat_task(args.chat_task, args.model, args.tenant, args.user)
        if args.json:
            print(json.dumps({"chat_task": flow}, ensure_ascii=False, indent=2))
        else:
            print(
                f"\n一句话走完整的对话入口（{args.tenant}/{args.user}，AGENT_MODE=pydantic_ai）："
            )
            for step in flow["steps"]:
                slot = step["expected_slot"]
                extra = f" 缺口={slot}" if slot else ""
                print(
                    f"  用户：{step['message']}\n"
                    f"    意图={step['intent']} 状态={step['task_status'] or '-'}{extra}\n"
                    f"    客服：{step['reply'][:90]}"
                )
            task = flow["task"] or {}
            routed = flow["routed"]
            print(
                f"  任务：{task.get('task_id')} status={task.get('status')} "
                f"routed={routed.get('intent')} source={routed.get('planning_source')} "
                f"approval_amount={(task.get('pending_approval') or {}).get('amount_cents')}"
            )
            print(f"  事件：{' -> '.join(flow['events'])}")
            usage = flow["usage"]
            print(
                f"  模型代价（这句对话）：calls={usage['calls']} "
                f"tokens={usage['input_tokens']}+{usage['output_tokens']} "
                f"wall={usage['latency_ms']:.0f} ms"
            )

    if args.live_task:
        outcomes = live_task(args.live_task, args.model, args.tenant, args.user)
        if args.json:
            print(json.dumps({"live_task": outcomes}, ensure_ascii=False, indent=2))
        else:
            print(
                f"\n同一个请求走 POST /api/v1/tasks（{args.tenant}/{args.user}，两份独立临时库）："
            )
            for mode, read in outcomes.items():
                print(
                    f"  AGENT_MODE={mode:11} intent={read['routed_intent']} "
                    f"source={read['planning_source']} status={read['status']} "
                    f"outcome={read['outcome']} approval_amount={read['pending_approval']}"
                )
                print(f"    summary: {read['summary']}")

    if summary["invented_order_ids"] or any(row.invented_order_id for row in rows):
        print("违反约束：模型给出了文本里不存在的 order_id。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
