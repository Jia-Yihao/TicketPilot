"""The shared rate-limit store: counters that survive a worker.

The fake client keeps these tests offline and deterministic. The Docker
integration test in ``tests/test_rate_limit_redis.py`` is the proof that the
real client speaks the same commands.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middleware import RateLimitMiddleware
from app.core.rate_limit import (
    RedisFixedWindow,
    TokenBucket,
    build_rate_limit_store,
)
from tests.conftest import ALICE


class FakeRedis:
    """Only the three commands the store uses, counted for assertions."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.calls = 0

    def incr(self, key: str) -> int:
        self.calls += 1
        if self.fail:
            raise ConnectionError("redis down")
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def expire(self, key: str, seconds: int) -> bool:
        if self.fail:
            raise ConnectionError("redis down")
        self.ttls[key] = seconds
        return True

    def ttl(self, key: str) -> int:
        if self.fail:
            raise ConnectionError("redis down")
        return self.ttls.get(key, -1)


def test_fixed_window_allows_the_budget_then_reports_retry_after() -> None:
    client = FakeRedis()
    store = RedisFixedWindow(client, 2, clock=lambda: 1000.0)

    assert store.consume("tenant:demo") == (True, 0)
    assert store.consume("tenant:demo") == (True, 0)
    allowed, retry_after = store.consume("tenant:demo")
    assert allowed is False
    assert 1 <= retry_after <= 60
    assert len(client.values) == 1


def test_fixed_window_starts_a_new_budget_in_the_next_minute() -> None:
    client = FakeRedis()
    now = [1000.0]
    store = RedisFixedWindow(client, 1, clock=lambda: now[0])

    assert store.consume("ip:1.2.3.4") == (True, 0)
    assert store.consume("ip:1.2.3.4")[0] is False

    now[0] += 60
    assert store.consume("ip:1.2.3.4") == (True, 0)
    assert len(client.values) == 2


def test_a_dead_store_degrades_to_the_local_bucket_not_to_unlimited() -> None:
    store = RedisFixedWindow(FakeRedis(fail=True), 2)

    assert store.consume("tenant:demo") == (True, 0)
    assert store.consume("tenant:demo") == (True, 0)
    assert store.consume("tenant:demo")[0] is False


def test_redis_backend_without_a_url_refuses_to_start() -> None:
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        build_rate_limit_store(backend="redis", per_minute=10)


def test_memory_backend_is_still_the_default() -> None:
    assert isinstance(build_rate_limit_store(backend="memory", per_minute=10), TokenBucket)


def test_middleware_uses_the_injected_shared_store() -> None:
    client = FakeRedis()
    application = FastAPI()

    @application.get("/api/v1/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    application.add_middleware(
        RateLimitMiddleware,
        tenant_per_minute=2,
        ip_per_minute=1000,
        tenant_limiter=RedisFixedWindow(client, 2, clock=lambda: 1000.0),
    )
    http = TestClient(application)

    assert http.get("/api/v1/ping", headers=ALICE).status_code == 200
    assert http.get("/api/v1/ping", headers=ALICE).status_code == 200
    rejected = http.get("/api/v1/ping", headers=ALICE)
    assert rejected.status_code == 429
    assert rejected.json()["error"]["details"]["limit"] == "tenant"
    # Three requests reached the shared counter, so two workers would have
    # seen the same budget rather than one each.
    assert sum(client.values.values()) == 3


def test_redis_window_handles_a_missing_ttl(monkeypatch: Any) -> None:
    client = FakeRedis()
    store = RedisFixedWindow(client, 0, clock=lambda: 1000.0)
    assert store.consume("anything") == (True, 0)

    store = RedisFixedWindow(client, 1, clock=lambda: 1000.0)
    assert store.consume("k") == (True, 0)
    monkeypatch.setattr(client, "ttl", lambda key: -1)
    assert store.consume("k") == (False, 1)
