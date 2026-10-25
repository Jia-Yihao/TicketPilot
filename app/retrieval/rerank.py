"""Optional rerank channel (Cohere-style ``/rerank``), designed to fail open.

The reranker is an *addition* to the retrieval chain, so every failure mode
(timeout, non-2xx, malformed body, missing endpoint) must leave the original
order untouched instead of breaking the answer. A circuit breaker stops calling
a broken reranker for a while so the request latency does not pay for it on
every query.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

import httpx


class RerankClient:
    """Calls a rerank endpoint and never raises: it returns ``None`` instead."""

    def __init__(
        self,
        url: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout: float = 5.0,
        top_n: int = 20,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
    ) -> None:
        self.url = url
        self.model = model
        self._api_key = api_key
        self._timeout = timeout
        self._top_n = top_n
        self._failure_threshold = max(1, failure_threshold)
        self._cooldown_seconds = cooldown_seconds
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def failures(self) -> int:
        """Consecutive failures observed so far."""
        return self._failures

    def rerank(
        self, query: str, documents: Sequence[tuple[str, str]]
    ) -> list[tuple[str, float]] | None:
        """Return reranked ``(id, score)`` pairs, or ``None`` when unusable.

        ``None`` means "keep your own order"; the caller decides what that
        order is, so a broken reranker can never lose the retrieved chunks.
        """
        if not documents:
            return []
        now = time.monotonic()
        if self._opened_at is not None:
            if now - self._opened_at < self._cooldown_seconds:
                return None
            self._opened_at = None
            self._failures = 0

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = httpx.post(
                self.url,
                json={
                    "model": self.model,
                    "query": query,
                    "documents": [text for _, text in documents],
                    "top_n": self._top_n,
                },
                headers=headers,
                timeout=self._timeout,
            )
            if response.is_error:
                raise RuntimeError(
                    f"rerank endpoint returned HTTP {response.status_code}"
                )
            payload = response.json()
            results = payload.get("results") or []
            ranked: list[tuple[str, float]] = []
            for item in results:
                index = int(item["index"])
                if not 0 <= index < len(documents):
                    continue
                ranked.append((documents[index][0], float(item.get("relevance_score", 0.0))))
        except Exception:
            # Fail open: the reranker is optional, so any error keeps the
            # caller's own ranking instead of breaking the answer.
            self._failures += 1
            if self._failures >= self._failure_threshold:
                self._opened_at = time.monotonic()
            return None
        self._failures = 0
        return ranked
