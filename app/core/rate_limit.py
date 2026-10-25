"""Rate-limit stores behind one interface.

The middleware needs one question answered per request: may this key spend
another request, and if not, how long should it wait? Two stores answer it:

* ``TokenBucket`` is the default. It is exact and cheap but process-local, so
  with N workers a tenant gets N times the configured budget.
* ``RedisFixedWindow`` makes the budget a shared fact. It counts requests per
  minute with ``INCR`` + ``EXPIRE``, which is one round trip and a coarse
  boundary: a tenant can spend two windows back to back at a minute edge.
  That is the documented trade for not running Lua on every request.

A Redis error falls back to the local bucket instead of failing open. A dead
store must not turn into an unlimited budget; it degrades to the old
per-process guarantee, which is honest but not authoritative.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "RateLimitStore",
    "RedisFixedWindow",
    "TokenBucket",
    "build_rate_limit_store",
]

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60
_KEY_PREFIX = "ticketpilot:rate"


class RateLimitStore(Protocol):
    per_minute: int

    def consume(self, key: str) -> tuple[bool, int]: ...


@dataclass
class _Bucket:
    tokens: float
    updated: float


class TokenBucket:
    """One key's budget, refilled continuously at ``per_minute / 60``."""

    def __init__(self, per_minute: int, *, clock: Any = time.monotonic) -> None:
        self.per_minute = per_minute
        self.capacity = float(max(1, per_minute))
        self.rate = per_minute / 60.0
        self.clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def consume(self, key: str) -> tuple[bool, int]:
        if self.per_minute <= 0:
            return True, 0
        now = self.clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self.capacity, updated=now)
                self._buckets[key] = bucket
            else:
                elapsed = max(0.0, now - bucket.updated)
                bucket.tokens = min(
                    self.capacity, bucket.tokens + elapsed * self.rate
                )
                bucket.updated = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0
            retry_after = math.ceil((1.0 - bucket.tokens) / self.rate)
            self._prune(now)
            return False, max(1, retry_after)

    def _prune(self, now: float) -> None:
        if len(self._buckets) < 4096:
            return
        stale = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket.updated > 600.0
        ]
        for key in stale:
            self._buckets.pop(key, None)


class RedisFixedWindow:
    """A shared per-minute counter; one ``INCR`` and one ``EXPIRE`` per call.

    The key embeds the minute so old windows expire on their own; the extra
    five seconds of TTL keep a request near the boundary from recreating a
    counter that is about to vanish.
    """

    def __init__(
        self,
        client: Any,
        per_minute: int,
        *,
        window_seconds: int = _WINDOW_SECONDS,
        clock: Any = time.time,
    ) -> None:
        self.client = client
        self.per_minute = per_minute
        self.window_seconds = max(1, window_seconds)
        self.clock = clock
        self._fallback = TokenBucket(per_minute)

    def consume(self, key: str) -> tuple[bool, int]:
        if self.per_minute <= 0:
            return True, 0
        window = int(self.clock() // self.window_seconds)
        redis_key = f"{_KEY_PREFIX}:{key}:{window}"
        try:
            count = int(self.client.incr(redis_key))
            if count == 1:
                self.client.expire(redis_key, self.window_seconds + 5)
            if count <= self.per_minute:
                return True, 0
            ttl = int(self.client.ttl(redis_key) or 1)
            return False, max(1, min(ttl, self.window_seconds))
        except Exception:
            logger.warning(
                "rate-limit store unavailable; using the local bucket for %s", key,
                exc_info=True,
            )
            return self._fallback.consume(key)


def build_rate_limit_store(
    *,
    backend: str,
    per_minute: int,
    redis_url: str = "",
    timeout_seconds: float = 0.5,
) -> RateLimitStore:
    """Build the configured store; a missing client/URL is a startup error.

    The Redis client is imported only when selected, so a default install
    keeps its dependency set small. ``from_url`` does not connect here: the
    first request is where unavailability is noticed and the fallback applies.
    """
    if backend != "redis":
        return TokenBucket(per_minute)
    if not redis_url:
        raise RuntimeError("RATE_LIMIT_STORE=redis requires REDIS_URL.")
    try:
        import redis
    except ImportError as exc:  # pragma: no cover - exercised by a bare install
        raise RuntimeError(
            "RATE_LIMIT_STORE=redis requires the optional 'redis' dependency."
        ) from exc
    client = redis.Redis.from_url(
        redis_url,
        socket_timeout=timeout_seconds,
        socket_connect_timeout=timeout_seconds,
        decode_responses=True,
    )
    return RedisFixedWindow(client, per_minute)
