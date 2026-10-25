from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import ForbiddenError, ValidationAppError
from app.core.security import Identity
from app.models.enums import RiskLevel
from app.tools.base import ToolContext, ToolManifest, ToolResult
from app.tools.gateway import ToolGateway
from app.tools.registry import ToolRegistry
from app.tools.schema import validate_params


def _registry(manifest: ToolManifest, calls: list[dict]) -> ToolRegistry:
    registry = ToolRegistry()

    def handler(ctx: ToolContext, args: dict) -> ToolResult:
        calls.append(args)
        return ToolResult(ok=True, message="done")

    registry.register(manifest, handler)
    return registry


def _manifest(**overrides) -> ToolManifest:
    values = {
        "name": "read_order",
        "description": "read an order",
        "params": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "pattern": "^ORD-[A-Za-z0-9-]+$",
                    "hint": "订单号应为 ORD-… 形式",
                },
                "amount_cents": {"type": "integer", "minimum": 1},
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
    }
    values.update(overrides)
    return ToolManifest(**values)


def _gateway(app: FastAPI, manifest: ToolManifest, calls: list[dict]) -> ToolGateway:
    with app.state.session_factory() as session:
        return ToolGateway(session, _registry(manifest, calls))


def _ctx() -> ToolContext:
    return ToolContext(identity=Identity("demo", "alice", frozenset()), task_id="task-1")


def test_schema_rejects_a_missing_required_argument() -> None:
    violations = validate_params(_manifest().params, {})

    assert violations
    assert violations[0][0] == "order_id"


def test_schema_rejects_a_pattern_mismatch() -> None:
    violations = validate_params(_manifest().params, {"order_id": "12345"})

    assert violations == [("order_id", "订单号应为 ORD-… 形式")]


def test_schema_rejects_an_unknown_argument() -> None:
    violations = validate_params(_manifest().params, {"order_id": "ORD-1", "x": 1})

    assert ("x", "不支持的参数") in violations


def test_schema_rejects_a_wrong_type() -> None:
    violations = validate_params(
        _manifest().params, {"order_id": "ORD-1", "amount_cents": "abc"}
    )

    assert ("amount_cents", "类型应为 integer") in violations


def test_schema_rejects_a_value_below_the_minimum() -> None:
    violations = validate_params(
        _manifest().params, {"order_id": "ORD-1", "amount_cents": 0}
    )

    assert ("amount_cents", "不能小于 1") in violations


def test_schema_accepts_a_valid_argument_set() -> None:
    violations = validate_params(
        _manifest().params, {"order_id": "ORD-DEMO-1001", "amount_cents": 12900}
    )

    assert violations == []


def test_gateway_stops_a_bad_argument_before_the_handler(app: FastAPI) -> None:
    calls: list[dict] = []
    gateway = _gateway(app, _manifest(), calls)

    with pytest.raises(ValidationAppError) as excinfo:
        gateway.call("read_order", _ctx(), {"order_id": "not-an-order"})

    assert calls == []
    assert "ORD-" in excinfo.value.message


def test_gateway_reports_which_argument_is_wrong(app: FastAPI) -> None:
    calls: list[dict] = []
    gateway = _gateway(app, _manifest(), calls)

    with pytest.raises(ValidationAppError) as excinfo:
        gateway.call("read_order", _ctx(), {})

    assert "order_id" in excinfo.value.message
    assert excinfo.value.details["violations"]


def test_gateway_passes_valid_arguments_to_the_handler(app: FastAPI) -> None:
    calls: list[dict] = []
    gateway = _gateway(app, _manifest(), calls)

    result = gateway.call("read_order", _ctx(), {"order_id": "ORD-DEMO-1001"})

    assert result.ok is True
    assert calls == [{"order_id": "ORD-DEMO-1001"}]


def test_gateway_refuses_a_caller_without_the_required_scope(app: FastAPI) -> None:
    calls: list[dict] = []
    gateway = _gateway(app, _manifest(scopes=("support",)), calls)

    with pytest.raises(ForbiddenError):
        gateway.call("read_order", _ctx(), {"order_id": "ORD-DEMO-1001"})

    assert calls == []


def test_gateway_allows_a_caller_with_the_required_scope(app: FastAPI) -> None:
    calls: list[dict] = []
    gateway = _gateway(app, _manifest(scopes=("support",)), calls)
    ctx = ToolContext(
        identity=Identity("demo", "alice", frozenset({"support"})), task_id="task-1"
    )

    gateway.call("read_order", ctx, {"order_id": "ORD-DEMO-1001"})

    assert calls == [{"order_id": "ORD-DEMO-1001"}]


def test_gateway_requires_an_idempotency_key_when_declared(app: FastAPI) -> None:
    calls: list[dict] = []
    gateway = _gateway(app, _manifest(idempotency="required"), calls)

    with pytest.raises(ValidationAppError):
        gateway.call("read_order", _ctx(), {"order_id": "ORD-DEMO-1001"})

    assert calls == []


def test_builtin_manifests_declare_their_contract(app: FastAPI) -> None:
    with app.state.session_factory() as session:
        services = app.state.runtime._services(session)
        manifests = services.gateway.registry.manifests()

    by_name = {manifest.name: manifest for manifest in manifests}
    assert set(by_name) == {
        "get_order",
        "get_logistics",
        "search_policy",
        "create_ticket",
        "check_refund_eligibility",
        "request_refund",
    }
    for manifest in manifests:
        assert manifest.version == "2"
        assert manifest.description
        assert manifest.params.get("type") == "object"
        assert manifest.timeout_ms > 0
        assert manifest.result_schema.get("type") == "object"

    assert by_name["request_refund"].requires_approval is True
    assert by_name["request_refund"].risk_level is RiskLevel.R3
    assert by_name["create_ticket"].idempotency == "required"
    assert by_name["search_policy"].risk_level is RiskLevel.R0


def test_a_malformed_order_id_becomes_a_question_not_a_failure(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/v1/tasks",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={
            "request": "帮我查一下订单状态",
            "context": {"order_id": "BAD-ID"},
        },
    )
    assert created.status_code == 201
    task = created.json()

    assert task["status"] == "SUCCEEDED"
    assert task["result"]["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert "ORD-" in task["result"]["summary"]


def test_the_degradation_is_recorded_in_the_task_trace(client: TestClient) -> None:
    created = client.post(
        "/api/v1/tasks",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
        json={
            "request": "帮我查一下订单状态",
            "context": {"order_id": "BAD-ID"},
        },
    )
    task = created.json()

    trace = client.get(
        f"/api/v1/traces/{task['trace_id']}",
        headers={"X-Tenant-Id": "demo", "X-User-Id": "alice"},
    )
    assert trace.status_code == 200
    spans = trace.json()["spans"]
    degraded = [span for span in spans if span["kind"] == "guard"]
    assert degraded
    assert degraded[0]["attributes"]["degradation"] == "ask_user"
    assert degraded[0]["status"] == "ERROR"
