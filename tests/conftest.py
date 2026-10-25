from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.seed import seed_all

ALICE = {"X-Tenant-Id": "demo", "X-User-Id": "alice"}
BOB = {"X-Tenant-Id": "demo", "X-User-Id": "bob"}
REVIEWER = {"X-Tenant-Id": "demo", "X-User-Id": "reviewer", "X-Roles": "approver"}
CAROL = {"X-Tenant-Id": "acme", "X-User-Id": "carol"}


@pytest.fixture
def app_factory(tmp_path: Path) -> Iterator[Callable[..., FastAPI]]:
    created: list[FastAPI] = []

    def factory(**overrides: object) -> FastAPI:
        index = len(created)
        values: dict[str, object] = {
            "environment": "test",
            "database_url": f"sqlite+pysqlite:///{tmp_path / f'test-{index}.db'}",
            "auto_create_tables": True,
            "mock_refund_mode": "success",
        }
        values.update(overrides)
        application = create_app(Settings(**values))
        seed_all(application.state.session_factory)
        created.append(application)
        return application

    yield factory

    for application in created:
        application.state.engine.dispose()


@pytest.fixture
def app(app_factory: Callable[..., FastAPI]) -> FastAPI:
    return app_factory()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)
