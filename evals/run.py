from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.time import utcnow
from app.main import create_app
from app.models.approval import Approval
from app.models.audit import AuditEvent
from app.models.commerce import Logistics, Order, Refund
from app.models.conversation import Conversation, ConversationMessage
from app.models.enums import ApprovalStatus
from app.models.task import Task, TaskEvent
from app.models.ticket import Ticket
from app.models.tool_execution import ToolExecution
from app.seed import seed_all
from app.tools.base import ToolManifest
from app.tools.executor import render_receipt

try:
    from evals.fixtures import apply_fixtures
except ModuleNotFoundError:  # `python evals/run.py` puts evals/ on sys.path
    from fixtures import apply_fixtures

_ORDER_ID = re.compile(r"ORD-[A-Za-z0-9-]+", re.IGNORECASE)
MIN_GOLDEN_CASES = 40

_REQUIRED_CASE_KEYS = (
    "id",
    "tenant",
    "user",
    "request",
    "kind",
    "expected_intent",
    "expected_outcome",
    "expected_tool",
    "expect",
)


def _headers(actor: dict[str, Any]) -> dict[str, str]:
    values = {
        "X-Tenant-Id": str(actor["tenant"]),
        "X-User-Id": str(actor["user"]),
    }
    if actor.get("roles"):
        values["X-Roles"] = str(actor["roles"])
    return values


def _expire_pending_approvals(app: FastAPI, task_id: str) -> int:
    """Move the task's pending approvals into the past so approve() sees expiry."""
    with app.state.session_factory() as session:
        rows = session.scalars(
            select(Approval).where(
                Approval.task_id == task_id,
                Approval.status == ApprovalStatus.PENDING,
            )
        ).all()
        for row in rows:
            row.expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
        return len(rows)


def _execute(
    app: FastAPI, client: TestClient, case: dict[str, Any]
) -> tuple[Any, dict[str, Any], str, list[dict[str, Any]]]:
    payload: dict[str, Any] = {"request": case["request"]}
    if case.get("context") is not None:
        payload["context"] = case["context"]
    response = client.post(
        "/api/v1/tasks",
        headers=_headers(case),
        json=payload,
    )
    body = response.json()
    trace_id = str(body.get("trace_id") or "")
    task_id = str(body.get("task_id") or "")

    steps: list[dict[str, Any]] = []
    for step in case.get("steps", []):
        action = step["action"]
        if action == "expire_approval":
            expired = _expire_pending_approvals(app, task_id)
            steps.append({"action": action, "expired": expired})
            continue
        if action in {"approve", "reject"}:
            decision = "APPROVE" if action == "approve" else "REJECT"
            response = client.post(
                f"/api/v1/tasks/{task_id}/approve",
                headers=_headers(step),
                json={
                    "decision": step.get("decision", decision),
                    "reason": step.get("reason"),
                    "approval_id": step.get("approval_id"),
                },
            )
        elif action == "cancel":
            response = client.post(
                f"/api/v1/tasks/{task_id}/cancel",
                headers=_headers(step),
            )
        elif action == "get_task":
            response = client.get(
                f"/api/v1/tasks/{task_id}",
                headers=_headers(step),
            )
        else:
            raise ValueError(f"Unsupported step: {action}")
        body = response.json()
        steps.append({"action": action, "response": response, "body": body})
    return response, body, trace_id, steps


def _step_failures(steps: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    for index, step in enumerate(steps):
        expect = step.get("expect") or {}
        response = step.get("response")
        if response is None:
            continue
        label = f"steps[{index}].{step['action']}"
        if "http_status" in expect and response.status_code != expect["http_status"]:
            failures.append(
                f"{label}.http_status: expected {expect['http_status']}, "
                f"got {response.status_code}"
            )
        text = json.dumps(step.get("body") or {}, ensure_ascii=False)
        for needle in expect.get("contains", []):
            if needle not in text:
                failures.append(f"{label}.contains: missing {needle!r}")
    return failures


def _evaluate(
    app: FastAPI,
    case: dict[str, Any],
    response: Any,
    body: dict[str, Any],
    steps: list[dict[str, Any]],
) -> list[str]:
    expect = case["expect"]
    failures: list[str] = _step_failures(steps)
    text = json.dumps(body, ensure_ascii=False)

    if response.status_code != expect.get("http_status", 201):
        failures.append(
            f"http_status: expected {expect.get('http_status', 201)}, got {response.status_code}"
        )

    if "task_status" in expect and body.get("status") != expect["task_status"]:
        failures.append(f"task_status: expected {expect['task_status']}, got {body.get('status')}")

    actual_outcome = (body.get("result") or {}).get("outcome")
    if actual_outcome != case["expected_outcome"]:
        failures.append(
            f"result.outcome: expected {case['expected_outcome']}, got {actual_outcome}"
        )

    if "error_code" in expect:
        actual = (body.get("result") or {}).get("error_code")
        if actual != expect["error_code"]:
            failures.append(
                f"result.error_code: expected {expect['error_code']}, got {actual}"
            )

    for needle in expect.get("contains", []):
        if needle not in text:
            failures.append(f"contains: missing {needle!r}")

    for needle in expect.get("not_contains", []):
        if needle in text:
            failures.append(f"not_contains: leaked {needle!r}")

    excluded_versions = set(expect.get("evidence_versions_exclude", []))
    evidence = (body.get("result") or {}).get("evidence") or []
    seen_versions = {item.get("version") for item in evidence if isinstance(item, dict)}
    leaked = excluded_versions & seen_versions
    if leaked:
        failures.append(f"evidence_versions_exclude: found {sorted(leaked)}")

    if "pending_amount_cents" in expect:
        actual = (body.get("pending_approval") or {}).get("amount_cents")
        if actual != expect["pending_amount_cents"]:
            failures.append(
                f"pending_approval.amount_cents: expected "
                f"{expect['pending_amount_cents']}, got {actual}"
            )

    if "latest_approval_status" in expect:
        actual = (body.get("latest_approval") or {}).get("status")
        if actual != expect["latest_approval_status"]:
            failures.append(
                f"latest_approval.status: expected "
                f"{expect['latest_approval_status']}, got {actual}"
            )

    if "order_refundable_cents" in expect:
        match = _ORDER_ID.search(case["request"])
        with app.state.session_factory() as session:
            order = session.get(Order, match.group(0).upper()) if match else None
        if order is None:
            failures.append("order_refundable_cents: order not found")
        elif order.refundable_cents != expect["order_refundable_cents"]:
            failures.append(
                "order_refundable_cents: expected "
                f"{expect['order_refundable_cents']}, got {order.refundable_cents}"
            )

    return failures


def _tool_manifest(app: FastAPI, name: str) -> ToolManifest | None:
    with app.state.session_factory() as session:
        registry = app.state.runtime._services(session).gateway.registry
        for manifest in registry.manifests():
            if manifest.name == name:
                return manifest
    return None


def _trajectory_failures(
    app: FastAPI,
    case: dict[str, Any],
    trace_body: dict[str, Any] | None,
    body: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    expect = case["expect"]
    spans = (trace_body or {}).get("spans") or []
    tool_spans = [span for span in spans if span["kind"] == "tool"]
    actual_tools = [span["name"] for span in tool_spans]

    expected_tools = case.get("expected_tools")
    if expected_tools is None:
        expected_tool = case["expected_tool"]
        expected_tools = [expected_tool] if expected_tool else []
    if actual_tools != expected_tools:
        failures.append(
            f"trace.tools: expected {expected_tools}, got {actual_tools}"
        )

    by_name: dict[str, dict[str, Any]] = {}
    for span in tool_spans:
        by_name.setdefault(span["name"], span)

    for tool, expected_params in (expect.get("tool_params") or {}).items():
        span = by_name.get(tool)
        if span is None:
            failures.append(f"trace.{tool}.params: tool span not found")
            continue
        actual_params = span["attributes"].get("params") or {}
        for key, value in expected_params.items():
            actual = actual_params.get(key)
            if actual != value:
                failures.append(
                    f"trace.{tool}.params.{key}: expected {value!r}, got {actual!r}"
                )

    if "degradation" in expect:
        guards = [
            span
            for span in spans
            if span["kind"] == "guard" and span["name"].startswith("tool.degraded.")
        ]
        actual = [span["attributes"].get("degradation") for span in guards]
        if expect["degradation"] not in actual:
            failures.append(
                f"trace.degradation: expected {expect['degradation']!r}, got {actual}"
            )
        if expect.get("degradation_status"):
            statuses = [span["status"] for span in guards]
            if expect["degradation_status"] not in statuses:
                failures.append(
                    "trace.degradation.status: expected "
                    f"{expect['degradation_status']!r}, got {statuses}"
                )

    if expect.get("no_degradation"):
        degraded = [
            span["name"]
            for span in spans
            if span["kind"] == "guard" and span["name"].startswith("tool.degraded.")
        ]
        if degraded:
            failures.append(f"trace.degradation: expected none, got {degraded}")

    route = next((span for span in spans if span["name"] == "task.route"), None)
    actual_intent = (route or {}).get("attributes", {}).get("intent")
    if actual_intent != case["expected_intent"]:
        failures.append(
            f"trace.task.route.intent: expected {case['expected_intent']}, got {actual_intent}"
        )

    receipt_from = expect.get("receipt_from")
    if receipt_from:
        manifest = _tool_manifest(app, receipt_from)
        data = (body.get("result") or {}).get("data") or {}
        if manifest is None:
            failures.append(f"receipt.{receipt_from}: tool is not registered")
        else:
            rendered = render_receipt(manifest, data)
            summary = (body.get("result") or {}).get("summary")
            if not rendered:
                failures.append(
                    f"receipt.{receipt_from}: result.data cannot fill the template"
                )
            elif summary != rendered:
                failures.append(
                    f"receipt.{receipt_from}: summary expected {rendered!r}, got {summary!r}"
                )
    return failures


def _dataset_tenants(dataset: dict[str, Any]) -> set[str]:
    return {case["tenant"] for case in dataset["cases"] if case.get("tenant")}


def _validate_dataset(dataset: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    seen: set[str] = set()
    for index, case in enumerate(dataset.get("cases", [])):
        label = case.get("id", f"case[{index}]")
        missing = [key for key in _REQUIRED_CASE_KEYS if key not in case]
        if missing:
            failures.append(f"{label}: missing keys {missing}")
        if label in seen:
            failures.append(f"{label}: duplicate case id")
        seen.add(label)
        if case.get("expected_tool") is not None and not isinstance(
            case.get("expected_tool"), str
        ):
            failures.append(f"{label}: expected_tool must be a string or null")
    if len(dataset.get("cases", [])) < MIN_GOLDEN_CASES:
        failures.append(
            f"dataset: expected at least {MIN_GOLDEN_CASES} cases, "
            f"found {len(dataset.get('cases', []))}"
        )
    return failures


def _leftover_state(
    session_factory: sessionmaker[Session], tenants: set[str]
) -> dict[str, int]:
    with session_factory() as session:
        task_ids = select(Task.id).where(Task.tenant_id.in_(tenants))
        counts = {
            "tasks": session.scalar(
                select(Task.id).where(Task.tenant_id.in_(tenants)).limit(1)
            ),
            "approvals": session.scalar(
                select(Approval.id).where(Approval.task_id.in_(task_ids)).limit(1)
            ),
            "refunds": session.scalar(
                select(Refund.id).where(Refund.tenant_id.in_(tenants)).limit(1)
            ),
            "orders": session.scalar(
                select(Order.id).where(Order.tenant_id.in_(tenants)).limit(1)
            ),
        }
    return {name: int(1 if value else 0) for name, value in counts.items()}


def _reset_state(
    session_factory: sessionmaker[Session], tenants: set[str]
) -> dict[str, int]:
    """Clear demo transactional rows so the golden set sees a pristine run."""
    with session_factory() as session:
        task_ids = select(Task.id).where(Task.tenant_id.in_(tenants))
        order_ids = select(Order.id).where(Order.tenant_id.in_(tenants))
        conversation_ids = select(Conversation.id).where(
            Conversation.tenant_id.in_(tenants)
        )
        removed: dict[str, int] = {}
        for label, statement in (
            ("task_events", delete(TaskEvent).where(TaskEvent.task_id.in_(task_ids))),
            (
                "tool_executions",
                delete(ToolExecution).where(ToolExecution.task_id.in_(task_ids)),
            ),
            ("approvals", delete(Approval).where(Approval.task_id.in_(task_ids))),
            ("tasks", delete(Task).where(Task.tenant_id.in_(tenants))),
            (
                "conversation_messages",
                delete(ConversationMessage).where(
                    ConversationMessage.conversation_id.in_(conversation_ids)
                ),
            ),
            (
                "conversations",
                delete(Conversation).where(Conversation.tenant_id.in_(tenants)),
            ),
            ("refunds", delete(Refund).where(Refund.tenant_id.in_(tenants))),
            ("audit_events", delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenants))),
            ("tickets", delete(Ticket).where(Ticket.tenant_id.in_(tenants))),
            ("logistics", delete(Logistics).where(Logistics.order_id.in_(order_ids))),
            ("orders", delete(Order).where(Order.tenant_id.in_(tenants))),
        ):
            result = session.execute(statement)
            removed[label] = int(getattr(result, "rowcount", 0) or 0)
        session.commit()
    return removed


def _fetch_trace(
    client: TestClient, trace_id: str, headers: dict[str, str]
) -> dict[str, Any] | None:
    if not trace_id:
        return None
    response = client.get(f"/api/v1/traces/{trace_id}", headers=headers)
    if response.status_code != 200:
        return None
    return response.json()


def _run_dataset(
    dataset: dict[str, Any], database_url: str, reset: bool = False
) -> int:
    dataset_failures = _validate_dataset(dataset)
    if dataset_failures:
        for item in dataset_failures:
            print(f"DATASET {item}")
        return 2

    settings = Settings(
        environment="test",
        database_url=database_url,
        auto_create_tables=True,
    )
    app = create_app(settings)
    tenants = _dataset_tenants(dataset)
    if reset:
        removed = _reset_state(app.state.session_factory, tenants)
        print(f"reset demo state for tenants {sorted(tenants)}: {removed}")
    else:
        leftover = _leftover_state(app.state.session_factory, tenants)
        if any(leftover.values()):
            app.state.engine.dispose()
            print(
                "目标数据库已有任务数据，Golden Set 需要干净库："
                f"{ {k: v for k, v in leftover.items() if v} }。"
                " 加 --reset 清空演示租户的事务数据，或换一个空库。",
            )
            return 2
    seed_all(app.state.session_factory)
    apply_fixtures(app.state.session_factory)
    client = TestClient(app)

    passed = 0
    failures: list[tuple[str, list[str]]] = []
    for case in dataset["cases"]:
        try:
            response, body, trace_id, steps = _execute(app, client, case)
            case_failures = _evaluate(app, case, response, body, steps)
            trace_body = _fetch_trace(client, trace_id, _headers(case))
            if trace_body is None:
                case_failures.append("trace: not found for the created task")
            else:
                case_failures.extend(
                    _trajectory_failures(app, case, trace_body, body)
                )
        except Exception as exc:  # one broken case must not hide the rest
            case_failures = [f"exception: {type(exc).__name__}: {exc}"]
        if case_failures:
            failures.append((case["id"], case_failures))
            print(f"FAIL {case['id']}")
            for item in case_failures:
                print(f"     - {item}")
        else:
            passed += 1
            print(f"PASS {case['id']}")

    total = len(dataset["cases"])
    print(f"\n{passed}/{total} golden cases passed")
    app.state.engine.dispose()
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the offline TicketPilot golden set.")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).with_name("golden_set.json"),
    )
    parser.add_argument(
        "--database-url",
        help="Run against this database instead of a temporary SQLite file.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear the dataset tenants' transactional rows before running.",
    )
    args = parser.parse_args()

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    if args.database_url:
        return _run_dataset(dataset, args.database_url, reset=args.reset)

    with tempfile.TemporaryDirectory() as directory:
        url = f"sqlite+pysqlite:///{Path(directory) / 'evals.db'}"
        return _run_dataset(dataset, url)


if __name__ == "__main__":
    sys.exit(main())
