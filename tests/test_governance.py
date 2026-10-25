from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import ApprovalRequiredError, NotFoundError
from app.core.security import Identity
from app.models.commerce import Order
from app.models.enums import RiskLevel
from app.services.approvals import canonical_digest, refund_payload
from app.tools.base import ToolContext, ToolManifest
from app.tools.registry import ToolRegistry


def test_request_refund_requires_approval_at_the_gateway(app: FastAPI) -> None:
    with app.state.session_factory() as session:
        services = app.state.runtime._services(session)
        with pytest.raises(ApprovalRequiredError):
            services.gateway.call(
                "request_refund",
                ToolContext(
                    identity=Identity("demo", "alice", frozenset()),
                    task_id="task-without-approval",
                ),
                {},
            )


def test_unknown_tool_is_not_dispatched(app: FastAPI) -> None:
    with app.state.session_factory() as session:
        services = app.state.runtime._services(session)
        with pytest.raises(NotFoundError):
            services.gateway.call(
                "delete_all_orders",
                ToolContext(
                    identity=Identity("demo", "alice", frozenset()),
                    task_id="task-x",
                ),
                {},
            )


def test_duplicate_tool_registration_is_rejected() -> None:
    registry = ToolRegistry()
    manifest = ToolManifest(
        name="read_only",
        risk_level=RiskLevel.R0,
        side_effect=False,
        requires_approval=False,
        description="test",
    )
    registry.register(manifest, lambda ctx, args: None)
    with pytest.raises(ValueError):
        registry.register(manifest, lambda ctx, args: None)


def test_digest_is_canonical_and_binding_changes_with_version() -> None:
    order = Order(
        id="ORD-DEMO-1001",
        tenant_id="demo",
        user_id="alice",
        total_cents=12900,
        refundable_cents=12900,
        currency="CNY",
        version=1,
    )
    first = canonical_digest(refund_payload(order, 12900, "refund-policy-v3"))
    second = canonical_digest(refund_payload(order, 12900, "refund-policy-v3"))
    changed = canonical_digest(refund_payload(order, 12800, "refund-policy-v3"))

    assert first == second
    assert first != changed


def _prod_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "prod",
        "database_url": "postgresql+psycopg://user:pass@localhost/db",
        "auto_create_tables": False,
        "identity_adapter": "oidc",
        "refund_provider": "http",
        "docs_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_production_requires_postgres_migrations_and_secure_adapters() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="prod", database_url="sqlite+pysqlite:///./x.db")
    with pytest.raises(ValidationError):
        Settings(
            environment="prod",
            database_url="postgresql+psycopg://user:pass@localhost/db",
            auto_create_tables=True,
        )
    with pytest.raises(ValidationError):
        _prod_settings(identity_adapter="dev_headers")
    with pytest.raises(ValidationError):
        _prod_settings(refund_provider="mock")
    with pytest.raises(ValidationError):
        _prod_settings(docs_enabled=True)

    settings = _prod_settings()
    assert settings.environment == "prod"


def test_app_refuses_unimplemented_production_adapters() -> None:
    from app.main import create_app

    with pytest.raises(RuntimeError, match="OIDC"):
        create_app(_prod_settings())


def test_docs_can_be_disabled(app_factory) -> None:
    app = app_factory(docs_enabled=False)
    client = TestClient(app)

    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert "docs" not in client.get("/").json()
