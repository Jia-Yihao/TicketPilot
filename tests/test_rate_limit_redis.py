"""Real Redis: the budget is shared between two app instances.

This is the integration proof the fake-client tests cannot give: two
``create_app`` instances, each with its own SQLite database and its own Redis
client object, enforce one tenant budget because they count the same key.

Run with a Redis endpoint:

    TICKETPILOT_TEST_REDIS_URL=redis://127.0.0.1:56379/0 \
        python -m pytest tests/test_rate_limit_redis.py
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.rate_limit import build_rate_limit_store
from app.main import create_app
from app.seed import seed_all
from tests.conftest import ALICE

REDIS_URL = os.getenv("TICKETPILOT_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(
    not REDIS_URL,
    reason="set TICKETPILOT_TEST_REDIS_URL to run the shared-budget test",
)


def _clean(key: str) -> None:
    import redis

    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    window = int(time.time() // 60)
    client.delete(f"ticketpilot:rate:{key}:{window}")


def _app(tmp_path: Path, name: str) -> FastAPI:
    application = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+pysqlite:///{tmp_path / f'{name}.db'}",
            auto_create_tables=True,
            rate_limit_store="redis",
            redis_url=str(REDIS_URL),
            rate_limit_tenant_per_minute=2,
            rate_limit_ip_per_minute=1000,
        )
    )
    seed_all(application.state.session_factory)
    return application


def test_two_app_instances_share_one_tenant_budget(tmp_path: Path) -> None:
    _clean("tenant:demo")
    first = TestClient(_app(tmp_path, "worker-one"))
    second = TestClient(_app(tmp_path, "worker-two"))

    assert first.get("/api/v1/tasks", headers=ALICE).status_code == 200
    assert second.get("/api/v1/tasks", headers=ALICE).status_code == 200

    rejected = second.get("/api/v1/tasks", headers=ALICE)
    assert rejected.status_code == 429
    assert rejected.json()["error"]["details"]["limit"] == "tenant"
    # The third request was refused by the counter the first instance wrote.
    assert int(rejected.headers["Retry-After"]) >= 1


def test_two_stores_share_one_counter() -> None:
    _clean("itest:shared")
    first = build_rate_limit_store(
        backend="redis", per_minute=2, redis_url=str(REDIS_URL)
    )
    second = build_rate_limit_store(
        backend="redis", per_minute=2, redis_url=str(REDIS_URL)
    )

    assert first.consume("itest:shared") == (True, 0)
    assert second.consume("itest:shared") == (True, 0)
    assert second.consume("itest:shared")[0] is False
