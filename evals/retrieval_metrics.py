"""Offline retrieval evaluation for the support agent.

No LLM and no network: the policy search is scored against a labelled query
set, so a retrieval change can be judged by a number instead of by feel.

Metrics
    recall@k    share of the labelled relevant chunks that appear in the top k
    mrr         reciprocal rank of the first relevant chunk
    ndcg@k      rank-discounted gain of the top k
    intent      share of queries routed to the expected support intent

Hard constraints
    stale_hits      hits from a document that is not ACTIVE or out of its
                    effective window on the evaluation date
    cross_tenant   hits that belong to another tenant

Channels
    ``--channels`` compares retrieval channels on the same labelled set:

    bm25      the shipped baseline (sparse BM25 only);
    dense     the dense channel alone, measured through the same hybrid search
              with its sparse channel silenced; it is an evaluation view, not a
              deployment the product offers;
    hybrid    sparse + dense, fused with RRF. Without ``EMBEDDING_BASE_URL``
              the dense channel uses the offline hashing stand-in, which adds
              a character level view but no semantics;
    rerank    hybrid plus the rerank endpoint; it needs ``RERANK_URL`` and is
              skipped when none is configured.

    The gate thresholds are applied to the first channel in the list, which is
    the one the deployment ships; the other channels are reported as
    comparison numbers.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.routing import classify, extract_order_id
from app.agent.support_agent import classify_support
from app.core.config import Settings
from app.core.security import Identity
from app.core.time import utcnow
from app.db.session import build_engine, build_session_factory, create_all
from app.models.document import Document, DocumentChunk
from app.retrieval.build import build_reranker
from app.retrieval.dense import (
    EmbeddingProvider,
    HashingEmbedder,
    OpenAICompatibleEmbedder,
    store_embeddings,
)
from app.retrieval.hybrid import HybridPolicySearch
from app.retrieval.rerank import RerankClient
from app.retrieval.search import (
    PolicySearch,
    PolicySearchService,
    RetrievedChunk,
    ScoredChunk,
)
from app.seed import seed_all
from app.services.catalog import CatalogService

DATASET_PATH = Path(__file__).with_name("support_set.json")
DEFAULT_K = (1, 3, 5)
CHANNELS = ("bm25", "dense", "hybrid", "rerank")


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    query: str
    intent_expected: str
    intent_actual: str
    intent_ok: bool
    recall: dict[int, float]
    mrr: float
    ndcg: float
    retrieved: tuple[str, ...]
    stale_hits: int
    cross_tenant_hits: int


@dataclass(slots=True)
class Report:
    channel: str = "bm25"
    cases: list[CaseResult] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    @property
    def routing_gaps(self) -> list[CaseResult]:
        return [case for case in self.cases if not case.intent_ok]


def _identity(case: dict[str, Any]) -> Identity:
    return Identity(
        tenant_id=case.get("tenant", "demo"),
        user_id=case.get("user", "alice"),
        roles=frozenset(),
    )


def _policy_id(document: Document) -> str:
    meta = document.meta or {}
    return str(meta.get("policy_id", ""))


def _is_stale(document: Document, today: date) -> bool:
    if document.status != "ACTIVE":
        return True
    if document.effective_from > today:
        return True
    return document.effective_to is not None and document.effective_to < today


def _dcg(gains: list[int]) -> float:
    return sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))


def evaluate(
    dataset: dict[str, Any],
    session_factory: sessionmaker[Session],
    *,
    k_values: tuple[int, ...] = DEFAULT_K,
    search_factory: Callable[[Session], PolicySearch] | None = None,
    channel: str = "bm25",
) -> Report:
    today = utcnow().date()
    max_k = max(k_values)
    report = Report(channel=channel)

    with session_factory() as session:
        rows = session.execute(
            select(DocumentChunk, Document).join(
                Document, Document.id == DocumentChunk.document_id
            )
        ).all()
        chunk_policy = {chunk.id: _policy_id(doc) for chunk, doc in rows}
        chunk_section = {chunk.id: chunk.section for chunk, _ in rows}
        chunk_document = {chunk.id: doc for chunk, doc in rows}

        catalog = CatalogService()
        retriever = (
            search_factory(session)
            if search_factory is not None
            else PolicySearchService(session, max_results=max_k, min_score=0.01)
        )

        for case in dataset["cases"]:
            identity = _identity(case)
            route = classify(case["query"], {})
            plan = classify_support(
                case["query"],
                route,
                has_catalog_match=catalog.resolve(case["query"]) is not None,
                message_order_id=extract_order_id(case["query"]),
            )
            intent_expected = case.get("intent", "")
            intent_actual = plan.intent.value

            policy_id = case.get("policy_id")
            sections = set(case.get("sections", []))
            if not policy_id:
                report.cases.append(
                    CaseResult(
                        case_id=case["id"],
                        query=case["query"],
                        intent_expected=intent_expected,
                        intent_actual=intent_actual,
                        intent_ok=intent_expected == intent_actual,
                        recall={},
                        mrr=0.0,
                        ndcg=0.0,
                        retrieved=(),
                        stale_hits=0,
                        cross_tenant_hits=0,
                    )
                )
                continue

            relevant = {
                chunk_id
                for chunk_id, chunk_policy_id in chunk_policy.items()
                if chunk_policy_id == policy_id and chunk_section[chunk_id] in sections
            }
            hits: list[RetrievedChunk] = retriever.search(
                identity, case["query"], limit=max_k
            )
            gains = [1 if hit.chunk_id in relevant else 0 for hit in hits]

            recall: dict[int, float] = {}
            for k in k_values:
                found = sum(gains[:k])
                recall[k] = found / len(relevant) if relevant else 0.0
            mrr = 0.0
            for index, gain in enumerate(gains):
                if gain:
                    mrr = 1.0 / (index + 1)
                    break
            ideal = [1] * min(len(relevant), max_k)
            ndcg = _dcg(gains[:max_k]) / _dcg(ideal) if ideal else 0.0

            stale_hits = 0
            cross_tenant_hits = 0
            for hit in hits:
                document = chunk_document[hit.chunk_id]
                if _is_stale(document, today):
                    stale_hits += 1
                if document.tenant_id != identity.tenant_id:
                    cross_tenant_hits += 1

            report.cases.append(
                CaseResult(
                    case_id=case["id"],
                    query=case["query"],
                    intent_expected=intent_expected,
                    intent_actual=intent_actual,
                    intent_ok=intent_expected == intent_actual,
                    recall=recall,
                    mrr=mrr,
                    ndcg=ndcg,
                    retrieved=tuple(
                        f"{hit.section}@{hit.document_version}" for hit in hits
                    ),
                    stale_hits=stale_hits,
                    cross_tenant_hits=cross_tenant_hits,
                )
            )

    retrieval_cases = [case for case in report.cases if case.recall]
    for k in k_values:
        values = [case.recall[k] for case in retrieval_cases]
        report.metrics[f"recall@{k}"] = sum(values) / len(values) if values else 0.0
    report.metrics["mrr"] = (
        sum(case.mrr for case in retrieval_cases) / len(retrieval_cases)
        if retrieval_cases
        else 0.0
    )
    report.metrics[f"ndcg@{max_k}"] = (
        sum(case.ndcg for case in retrieval_cases) / len(retrieval_cases)
        if retrieval_cases
        else 0.0
    )
    report.metrics["intent_accuracy"] = (
        sum(1 for case in report.cases if case.intent_ok) / len(report.cases)
        if report.cases
        else 0.0
    )
    report.metrics["stale_hits"] = float(sum(case.stale_hits for case in report.cases))
    report.metrics["cross_tenant_hits"] = float(
        sum(case.cross_tenant_hits for case in report.cases)
    )
    return report


def check_thresholds(report: Report, thresholds: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for name, limit in thresholds.items():
        actual = report.metrics.get(name)
        if actual is None:
            failures.append(f"{name}: metric missing")
            continue
        if name in {"stale_hits", "cross_tenant_hits"}:
            if actual > limit:
                failures.append(f"{name}: expected <= {limit}, got {actual:g}")
            continue
        if actual < limit:
            failures.append(f"{name}: expected >= {limit}, got {actual:.4f}")
    return failures


def format_report(report: Report, thresholds: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"channel: {report.channel}")
    lines.append("case                          intent          recall@1  recall@3  mrr    ndcg@5")
    lines.append("-" * 78)
    for case in report.cases:
        if not case.recall:
            lines.append(
                f"{case.case_id:<29} {case.intent_actual:<15} "
                f"{'(intent only)':<32}"
            )
            continue
        lines.append(
            f"{case.case_id:<29} {case.intent_actual:<15} "
            f"{case.recall[1]:<10.2f} {case.recall[3]:<10.2f} "
            f"{case.mrr:<6.2f} {case.ndcg:<6.2f}"
        )
    lines.append("")
    for name, value in report.metrics.items():
        limit = thresholds.get(name)
        marker = "" if limit is None else f"  (gate {limit})"
        lines.append(f"{name:<22} {value:.4f}{marker}")
    gaps = report.routing_gaps
    if gaps:
        lines.append("")
        lines.append(f"routing gaps ({len(gaps)}/{len(report.cases)}):")
        for case in gaps:
            lines.append(
                f"  - {case.case_id}: expected {case.intent_expected}, "
                f"got {case.intent_actual} :: {case.query}"
            )
    return "\n".join(lines)


def format_comparison(reports: list[Report]) -> str:
    lines = [
        "channel                recall@1  recall@3  recall@5   mrr  ndcg@5  stale  x-tenant"
    ]
    for report in reports:
        metrics = report.metrics
        lines.append(
            f"{report.channel:<22}"
            f"{metrics.get('recall@1', 0.0):<10.2f}"
            f"{metrics.get('recall@3', 0.0):<10.2f}"
            f"{metrics.get('recall@5', 0.0):<10.2f}"
            f"{metrics.get('mrr', 0.0):<6.2f}"
            f"{metrics.get('ndcg@5', 0.0):<6.2f}"
            f"{metrics.get('stale_hits', 0.0):<7.0f}"
            f"{metrics.get('cross_tenant_hits', 0.0):<9.0f}"
        )
    return "\n".join(lines)


def _dense_provider(settings: Settings) -> tuple[EmbeddingProvider, str]:
    """The dense provider for the evaluation, with a label for the report."""
    if settings.embedding_base_url and settings.embedding_model:
        api_key = (
            settings.embedding_api_key.get_secret_value()
            if settings.embedding_api_key
            else None
        )
        return (
            OpenAICompatibleEmbedder(
                settings.embedding_base_url,
                settings.embedding_model,
                api_key=api_key,
                timeout=settings.embedding_timeout_seconds,
            ),
            "endpoint",
        )
    return HashingEmbedder(settings.embedding_dimensions), "hashing"


def _sparse_factory(max_k: int) -> Callable[[Session], PolicySearch]:
    """The shipped sparse channel."""

    def factory(session: Session) -> PolicySearch:
        return PolicySearchService(session, max_results=max_k, min_score=0.01)

    return factory


class _SilencedSparse(PolicySearchService):
    """The sparse channel of the hybrid search, made to find nothing.

    Only ``ranked`` is neutered: ``visible_chunks`` still runs, so the dense
    channel is measured through exactly the production scoring path instead of
    a re-implementation of it.
    """

    def ranked(
        self,
        identity: Identity,
        query: str,
        *,
        today: date | None = None,
        limit: int | None = None,
    ) -> list[ScoredChunk]:
        return []


def _dense_only_factory(
    settings: Settings,
    provider: EmbeddingProvider | None,
    max_k: int,
) -> Callable[[Session], PolicySearch]:
    """The dense channel on its own, for a channel comparison."""

    def factory(session: Session) -> PolicySearch:
        return HybridPolicySearch(
            session,
            sparse=_SilencedSparse(session, max_results=max_k, min_score=0.01),
            embedder=provider,
            rrf_k=settings.retrieval_rrf_k,
            max_results=max_k,
            candidate_pool=settings.retrieval_candidate_pool,
            dense_min_score=settings.retrieval_dense_min_score,
        )

    return factory


def _hybrid_factory(
    settings: Settings,
    provider: EmbeddingProvider | None,
    max_k: int,
    reranker: RerankClient | None = None,
) -> Callable[[Session], PolicySearch]:
    """Sparse plus dense, optionally followed by the rerank endpoint."""

    def factory(session: Session) -> PolicySearch:
        return HybridPolicySearch(
            session,
            sparse=PolicySearchService(session, max_results=max_k, min_score=0.01),
            embedder=provider,
            reranker=reranker,
            rrf_k=settings.retrieval_rrf_k,
            max_results=max_k,
            candidate_pool=settings.retrieval_candidate_pool,
            dense_min_score=settings.retrieval_dense_min_score,
        )

    return factory


def _run(
    dataset: dict[str, Any],
    database_url: str,
    *,
    k_values: tuple[int, ...] = DEFAULT_K,
    channels: tuple[str, ...] = ("bm25",),
) -> tuple[list[Report], list[str]]:
    settings = Settings(
        environment="test",
        database_url=database_url,
        auto_create_tables=True,
    )
    engine = build_engine(settings.database_url)
    create_all(engine)
    session_factory = build_session_factory(engine)
    seed_all(session_factory)
    skipped: list[str] = []
    reports: list[Report] = []
    try:
        max_k = max(k_values)
        provider: EmbeddingProvider | None = None
        provider_label = ""
        if any(channel in {"dense", "hybrid", "rerank"} for channel in channels):
            provider, provider_label = _dense_provider(settings)
            with session_factory() as session:
                try:
                    store_embeddings(session, provider)
                    session.commit()
                except ValueError as exc:
                    # A database already embedded by another model stays as it
                    # is; the comparison reports the channel it can measure.
                    session.rollback()
                    skipped.append(f"dense index: {exc}")
                    provider, provider_label = None, "skipped"

        for channel in channels:
            if channel == "bm25":
                search_factory = _sparse_factory(max_k)
                label = "bm25"
            elif channel == "dense":
                search_factory = _dense_only_factory(settings, provider, max_k)
                label = f"dense({provider_label})"
            elif channel == "hybrid":
                search_factory = _hybrid_factory(settings, provider, max_k)
                label = f"hybrid({provider_label})"
            elif channel == "rerank":
                reranker = build_reranker(settings)
                if reranker is None:
                    skipped.append("rerank: no RERANK_URL configured")
                    continue
                search_factory = _hybrid_factory(settings, provider, max_k, reranker)
                label = f"rerank({provider_label})"
            else:
                skipped.append(f"{channel}: unknown channel")
                continue
            reports.append(
                evaluate(
                    dataset,
                    session_factory,
                    k_values=k_values,
                    search_factory=search_factory,
                    channel=label,
                )
            )
        return reports, skipped
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline TicketPilot retrieval evaluation."
    )
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument(
        "--database-url",
        help="Run against this database instead of a temporary SQLite file.",
    )
    parser.add_argument(
        "--channels",
        default="bm25",
        help=f"Comma separated channels to compare: {', '.join(CHANNELS)}.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the report as JSON instead of a table.",
    )
    args = parser.parse_args()

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    thresholds = dataset.get("thresholds", {})
    k_values = tuple(dataset.get("k_values", DEFAULT_K))
    channels = tuple(
        part.strip() for part in args.channels.split(",") if part.strip()
    )
    if not channels:
        print("no channel selected")
        return 2

    if args.database_url:
        reports, skipped = _run(
            dataset, args.database_url, k_values=k_values, channels=channels
        )
    else:
        with tempfile.TemporaryDirectory() as directory:
            url = f"sqlite+pysqlite:///{Path(directory) / 'retrieval.db'}"
            reports, skipped = _run(dataset, url, k_values=k_values, channels=channels)

    if not reports:
        print("no channel produced a report")
        return 2

    failures = check_thresholds(reports[0], thresholds)
    if args.json:
        print(
            json.dumps(
                {
                    "channels": [
                        {
                            "channel": report.channel,
                            "metrics": report.metrics,
                            "failures": check_thresholds(report, thresholds),
                            "routing_gaps": [
                                {
                                    "id": case.case_id,
                                    "query": case.query,
                                    "expected": case.intent_expected,
                                    "actual": case.intent_actual,
                                }
                                for case in report.routing_gaps
                            ],
                        }
                        for report in reports
                    ],
                    "gate_channel": reports[0].channel,
                    "thresholds": thresholds,
                    "failures": failures,
                    "skipped": skipped,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(format_report(reports[0], thresholds))
        if len(reports) > 1:
            print("")
            print(format_comparison(reports))
        for note in skipped:
            print(f"skipped: {note}")
        print("")
        if failures:
            print("GATE FAILED")
            for failure in failures:
                print(f"  - {failure}")
        else:
            print("GATE PASSED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
