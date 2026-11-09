"""Measure the rerank channel's latency and judge it against a budget.

The reranker is the one optional channel that costs real wall time: it is a
cross-process HTTP call that reorders the candidate chunks. "It makes recall@1
better" is not enough to ship it, because a chat answer pays that latency on
every query. This script turns the question into a number:

    python -m evals.rerank_latency --rerank-url http://127.0.0.1:7997/rerank

It walks the *shipped* rerank channel — the same sparse + dense + rerank
construction the ``rerank`` column of ``evals/retrieval_metrics.py`` measures —
and times only the HTTP call to the reranker, so the number is the price of the
channel and not the price of the whole retrieval.

The report gives mean / p50 / p95 / max / min, the candidate count that was
actually sent, the failure count, and a verdict against a budget.

The budget is a decision, not a measurement: ``--budget-ms`` defaults to
300 ms, which is the interactive ceiling a customer waiting on a chat answer
can accept before the channel stops being worth its cost. A channel that
misses the budget can still be worth it offline (batch re-scoring, test set
re-ranking); what the number decides is whether it may sit in the request
path.

``--enforce`` turns the verdict into an exit code, so the same script can be a
gate: exit 1 when p95 misses the budget or any call failed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.security import Identity
from app.db.session import build_engine, build_session_factory, create_all
from app.retrieval.build import build_reranker
from app.retrieval.dense import EmbeddingProvider, store_embeddings
from app.retrieval.hybrid import HybridPolicySearch
from app.retrieval.rerank import RerankClient
from app.retrieval.search import PolicySearchService
from app.seed import seed_all

DATASET_PATH = Path(__file__).with_name("support_set.json")
DEFAULT_BUDGET_MS = 300.0


class _TimedReranker:
    """Wraps the real client and records what every call cost."""

    def __init__(self, inner: RerankClient) -> None:
        self.inner = inner
        self.latencies_ms: list[float] = []
        self.candidate_counts: list[int] = []
        self.failures = 0

    def rerank(
        self, query: str, documents: list[tuple[str, str]]
    ) -> list[tuple[str, float]] | None:
        started = time.perf_counter()
        result = self.inner.rerank(query, documents)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.latencies_ms.append(elapsed_ms)
        self.candidate_counts.append(len(documents))
        if result is None:
            self.failures += 1
        return result


def _percentile(values: list[float], percentile: float) -> float:
    """Nearest-rank percentile, the same rule the trace metrics use."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, int(-(-percentile * len(ordered) // 100)))
    return ordered[min(rank, len(ordered)) - 1]


def _queries(dataset: dict[str, Any]) -> list[tuple[str, str, str]]:
    """``(case_id, query, tenant)`` for every labelled case."""
    rows: list[tuple[str, str, str]] = []
    for case in dataset.get("cases", []):
        query = str(case.get("query") or "").strip()
        if query:
            rows.append((str(case.get("id")), query, str(case.get("tenant", "demo"))))
    return rows


def _dense_provider(settings: Settings) -> EmbeddingProvider | None:
    """The dense provider, or ``None`` when there is no endpoint."""
    from app.retrieval.dense import OpenAICompatibleEmbedder

    if settings.embedding_base_url and settings.embedding_model:
        api_key = (
            settings.embedding_api_key.get_secret_value() if settings.embedding_api_key else None
        )
        return OpenAICompatibleEmbedder(
            settings.embedding_base_url,
            settings.embedding_model,
            api_key=api_key,
            timeout=settings.embedding_timeout_seconds,
        )
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure rerank latency against a budget.")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument(
        "--database-url",
        help="Run against this database instead of a temporary SQLite file.",
    )
    parser.add_argument(
        "--rerank-url",
        help="The rerank endpoint. Defaults to RERANK_URL from the environment.",
    )
    parser.add_argument(
        "--rerank-model",
        help="The rerank model name. Defaults to RERANK_MODEL from the environment.",
    )
    parser.add_argument(
        "--budget-ms",
        type=float,
        default=DEFAULT_BUDGET_MS,
        help="Interactive latency budget in milliseconds (default 300).",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="Warm-up calls that are not counted (default 1).",
    )
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="Exit 1 when the budget is missed or a call fails.",
    )
    args = parser.parse_args()

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    queries = _queries(dataset)
    if not queries:
        print("no queries in the dataset")
        return 2

    settings = Settings(environment="test", auto_create_tables=True)
    if args.database_url:
        database_url = args.database_url
        temporary = None
    else:
        temporary = tempfile.TemporaryDirectory()
        database_url = f"sqlite+pysqlite:///{Path(temporary.name) / 'rerank.db'}"
    settings = settings.model_copy(update={"database_url": database_url})
    if args.rerank_url:
        settings = settings.model_copy(update={"rerank_url": args.rerank_url})
    if args.rerank_model:
        settings = settings.model_copy(update={"rerank_model": args.rerank_model})
    settings = settings.model_copy(update={"retrieval_rerank_enabled": True})

    client = build_reranker(settings)
    if client is None:
        print("no rerank endpoint configured: set --rerank-url or RERANK_URL")
        return 2

    engine = build_engine(settings.database_url)
    create_all(engine)
    session_factory = build_session_factory(engine)
    seed_all(session_factory)

    provider = _dense_provider(settings)
    timed = _TimedReranker(client)
    latencies: list[float] = []
    empty_candidates = 0
    try:
        if provider is None:
            # Rerank sits on top of the fused chain; measuring it over a
            # sparse-only candidate list would report the wrong latency budget.
            raise SystemExit(
                "rerank latency needs the fused chain: set EMBEDDING_BASE_URL and EMBEDDING_MODEL"
            )
        with session_factory() as session:
            try:
                store_embeddings(session, provider)
                session.commit()
            except ValueError as exc:
                session.rollback()
                raise SystemExit(f"dense index refused: {exc}") from exc

        with session_factory() as session:
            search = HybridPolicySearch(
                session,
                sparse=PolicySearchService(
                    session, max_results=settings.policy_max_results, min_score=0.01
                ),
                embedder=provider,
                reranker=timed,
                max_results=settings.policy_max_results,
                candidate_pool=settings.retrieval_candidate_pool,
                dense_min_score=settings.retrieval_dense_min_score,
            )
            for index, (_case_id, query, tenant) in enumerate(queries):
                identity = Identity(tenant_id=tenant, user_id="alice", roles=frozenset())
                before = len(timed.latencies_ms)
                search.search(identity, query, limit=settings.policy_max_results)
                if len(timed.latencies_ms) == before:
                    empty_candidates += 1
                elif index < args.warmup:
                    timed.latencies_ms.pop()
                    timed.candidate_counts.pop()
            latencies = list(timed.latencies_ms)
    finally:
        engine.dispose()
        if temporary is not None:
            temporary.cleanup()

    if not latencies:
        print("no successful rerank call to report")
        return 2

    mean_ms = statistics.fmean(latencies)
    p50_ms = _percentile(latencies, 50)
    p95_ms = _percentile(latencies, 95)
    max_ms = max(latencies)
    min_ms = min(latencies)
    within_budget = p95_ms <= args.budget_ms
    candidates = timed.candidate_counts

    print(f"endpoint            {settings.rerank_url}")
    print(f"model               {settings.rerank_model}")
    print(
        f"queries             {len(latencies)} timed, {timed.failures} failed, "
        f"{empty_candidates} without candidates"
    )
    print(f"candidates          min {min(candidates)} / max {max(candidates)}")
    print(f"mean_ms             {mean_ms:.0f}")
    print(f"p50_ms              {p50_ms:.0f}")
    print(f"p95_ms              {p95_ms:.0f}")
    print(f"min_ms              {min_ms:.0f}")
    print(f"max_ms              {max_ms:.0f}")
    print(f"budget_ms           {args.budget_ms:.0f}")
    print(f"verdict             {'WITHIN BUDGET' if within_budget else 'OVER BUDGET'}")

    if args.enforce and (not within_budget or timed.failures):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
