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
    ``--channels`` names the states the deployment can be in:

    bm25      sparse only, which is what a local run without an embedding
              endpoint falls back to;
    hybrid    the shipped chain: sparse + dense, fused by per-channel z-scores
              at ``FUSION_ALPHA``. It needs a real embedding model — a
              ``--local-embedding`` path or ``EMBEDDING_BASE_URL`` — and is
              skipped, not faked, when neither is available;
    rerank    hybrid plus the rerank endpoint; it needs ``RERANK_URL`` and is
              skipped when none is configured.

Rewriting
    ``--rewrite {on,off}`` (default ``on``) puts every channel behind the query
    rewriter, so one run reports the same labels with and without the rewrite.
    ``off`` measures the pre-rewrite ranking. ``--json`` carries a per-case row
    (recall, mrr, ndcg, retrieved sections) so a single labelled case can be
    compared.

Depth
    ``--sweep`` scans the two knobs a retriever actually exposes and reports
    the curve instead of gating one point: ``--sweep-k`` is the answer length
    (top-k), ``--sweep-pools`` is the retrieval depth (top-n) each channel
    contributes before the cut. ``--candidate-pool`` sets top-n for a single
    run; ``--local-embedding`` scores a local sentence-transformers model, so
    a fine-tuned encoder can be measured without standing up an endpoint.

Measured on the 2,200-case set, 2026-11-09
    recall@3: BM25 .891, fused .923. Top-n saturates at the corpus size
    (15 chunks): 15/20/30/50 are identical, which is why the pool is 20.

    The fused score is a ranking score only: the escalation gate reads the
    channel score on ``RetrievedChunk.evidence()``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from collections.abc import Callable, Sequence
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
    OpenAICompatibleEmbedder,
    store_embeddings,
)
from app.retrieval.hybrid import HybridPolicySearch
from app.retrieval.rerank import RerankClient
from app.retrieval.rewrite import QueryRewriter, RewritingPolicySearch
from app.retrieval.search import (
    PolicySearch,
    PolicySearchService,
    RetrievedChunk,
)
from app.seed import seed_all
from app.services.catalog import CatalogService

DATASET_PATH = Path(__file__).with_name("retrieval_set.json")
DEFAULT_K = (1, 3, 5)
CHANNELS = ("bm25", "hybrid", "rerank")


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    query: str
    intent_expected: str | None
    intent_actual: str
    intent_ok: bool
    recall: dict[int, float]
    mrr: float
    ndcg: float
    retrieved: tuple[str, ...]
    stale_hits: int
    cross_tenant_hits: int
    layer: str = "legacy_gold"
    # ``ndcg@k`` for every scanned k, so a top-k sweep can read one run instead
    # of re-scoring the same ranking once per k. ``ndcg`` stays the value at the
    # largest k, which is what the gate reads.
    ndcg_by_k: dict[int, float] = field(default_factory=dict)


@dataclass(slots=True)
class Report:
    channel: str = "bm25"
    cases: list[CaseResult] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    layers: dict[str, dict[str, float]] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    @property
    def routing_gaps(self) -> list[CaseResult]:
        return [
            case for case in self.cases if case.intent_expected is not None and not case.intent_ok
        ]


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


def _layer(case: dict[str, Any]) -> str:
    """The reporting layer a labelled case belongs to.

    Layered retrieval cases declare ``label_quality`` (gold / silver) and a
    ``split``; grouping by both keeps the five thresholds aligned with the
    population they were written for.
    """
    quality = case.get("label_quality")
    if quality in {"gold", "silver"}:
        return f"{quality}_{case.get('split', 'test')}"
    return "legacy_gold"


def _summarise(cases: list[CaseResult], k_values: tuple[int, ...]) -> dict[str, float]:
    retrieval_cases = [case for case in cases if case.recall]
    metrics: dict[str, float] = {}
    for k in k_values:
        values = [case.recall[k] for case in retrieval_cases]
        metrics[f"recall@{k}"] = sum(values) / len(values) if values else 0.0
    metrics["mrr"] = (
        sum(case.mrr for case in retrieval_cases) / len(retrieval_cases) if retrieval_cases else 0.0
    )
    max_k = max(k_values)
    metrics[f"ndcg@{max_k}"] = (
        sum(case.ndcg for case in retrieval_cases) / len(retrieval_cases)
        if retrieval_cases
        else 0.0
    )
    for k in k_values:
        if k == max_k:
            continue
        values = [case.ndcg_by_k.get(k, 0.0) for case in retrieval_cases]
        metrics[f"ndcg@{k}"] = sum(values) / len(values) if values else 0.0
    with_intent = [case for case in cases if case.intent_expected is not None]
    if with_intent:
        metrics["intent_accuracy"] = sum(1 for case in with_intent if case.intent_ok) / len(
            with_intent
        )
    metrics["stale_hits"] = float(sum(case.stale_hits for case in cases))
    metrics["cross_tenant_hits"] = float(sum(case.cross_tenant_hits for case in cases))
    return metrics


def evaluate(
    dataset: dict[str, Any],
    session_factory: sessionmaker[Session],
    *,
    k_values: tuple[int, ...] = DEFAULT_K,
    search_factory: Callable[[Session], PolicySearch] | None = None,
    channel: str = "bm25",
    depth: int | None = None,
) -> Report:
    today = utcnow().date()
    max_k = max(k_values)
    # ``depth`` (top-n) is how far the retriever looks; ``max_k`` is how many
    # of those rows the answer may use. Keeping them separate is what lets a
    # sweep hold the candidate pool fixed while the answer length moves.
    limit = max(depth or max_k, max_k)
    report = Report(channel=channel)

    with session_factory() as session:
        rows = session.execute(
            select(DocumentChunk, Document).join(Document, Document.id == DocumentChunk.document_id)
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
            intent_expected = case.get("intent")
            intent_actual = plan.intent.value
            intent_ok = intent_expected is None or intent_expected == intent_actual
            layer = _layer(case)

            policy_id = case.get("policy_id")
            sections = set(case.get("sections", []))
            if not policy_id:
                report.cases.append(
                    CaseResult(
                        case_id=case["id"],
                        query=case["query"],
                        intent_expected=intent_expected,
                        intent_actual=intent_actual,
                        intent_ok=intent_ok,
                        recall={},
                        mrr=0.0,
                        ndcg=0.0,
                        retrieved=(),
                        stale_hits=0,
                        cross_tenant_hits=0,
                        layer=layer,
                    )
                )
                continue

            relevant = {
                chunk_id
                for chunk_id, chunk_policy_id in chunk_policy.items()
                if chunk_policy_id == policy_id and chunk_section[chunk_id] in sections
            }
            hits: list[RetrievedChunk] = retriever.search(identity, case["query"], limit=limit)
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
            ndcg_by_k = {
                k: (_dcg(gains[:k]) / _dcg([1] * min(len(relevant), k)) if relevant else 0.0)
                for k in k_values
            }

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
                    retrieved=tuple(f"{hit.section}@{hit.document_version}" for hit in hits),
                    stale_hits=stale_hits,
                    cross_tenant_hits=cross_tenant_hits,
                    layer=layer,
                    ndcg_by_k=ndcg_by_k,
                )
            )

    report.metrics = _summarise(report.cases, k_values)
    by_layer: dict[str, list[CaseResult]] = {}
    for case in report.cases:
        by_layer.setdefault(case.layer, []).append(case)
    report.layers = {
        layer: _summarise(cases, k_values) for layer, cases in sorted(by_layer.items())
    }
    return report


def _check_metrics(
    metrics: dict[str, float], thresholds: dict[str, Any], *, prefix: str = ""
) -> list[str]:
    failures: list[str] = []
    for name, limit in thresholds.items():
        actual = metrics.get(name)
        label = f"{prefix}.{name}" if prefix else name
        if actual is None:
            failures.append(f"{label}: metric missing")
            continue
        if name in {"stale_hits", "cross_tenant_hits"}:
            if actual > limit:
                failures.append(f"{label}: expected <= {limit}, got {actual:g}")
            continue
        if actual < limit:
            failures.append(f"{label}: expected >= {limit}, got {actual:.4f}")
    return failures


def check_thresholds(report: Report, thresholds: dict[str, Any]) -> list[str]:
    """Check a flat gate, or a per-layer gate when values are mappings.

    The layered retrieval set names each layer, so a threshold written under
    ``gold_test`` is never applied to silver cases.
    """
    failures: list[str] = []
    for name, limit in thresholds.items():
        if isinstance(limit, dict):
            layer = report.layers.get(name)
            if layer is None:
                failures.append(f"{name}: layer missing")
                continue
            failures.extend(_check_metrics(layer, limit, prefix=name))
            continue
        failures.extend(_check_metrics(report.metrics, {name: limit}))
    return failures


def _layer_counts(report: Report) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in report.cases:
        counts[case.layer] = counts.get(case.layer, 0) + 1
    return counts


def format_layers(report: Report) -> list[str]:
    if len(report.layers) <= 1 and "legacy_gold" in report.layers:
        return []
    counts = _layer_counts(report)
    lines = [
        "layer                 cases  recall@1  recall@3  recall@5   mrr  ndcg@5  stale  x-tenant"
    ]
    for layer, metrics in report.layers.items():
        lines.append(
            f"{layer:<21}"
            f"{counts.get(layer, 0):<7}"
            f"{metrics.get('recall@1', 0.0):<10.2f}"
            f"{metrics.get('recall@3', 0.0):<10.2f}"
            f"{metrics.get('recall@5', 0.0):<10.2f}"
            f"{metrics.get('mrr', 0.0):<6.2f}"
            f"{metrics.get('ndcg@5', 0.0):<8.2f}"
            f"{metrics.get('stale_hits', 0.0):<7.0f}"
            f"{metrics.get('cross_tenant_hits', 0.0):<9.0f}"
        )
    return lines


def format_report(report: Report, thresholds: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"channel: {report.channel}")
    if len(report.cases) <= 60:
        lines.append(
            "case                          intent          recall@1  recall@3  mrr    ndcg@5"
        )
        lines.append("-" * 78)
        for case in report.cases:
            if not case.recall:
                lines.append(f"{case.case_id:<29} {case.intent_actual:<15} {'(intent only)':<39}")
                continue
            lines.append(
                f"{case.case_id:<29} {case.intent_actual:<15} "
                f"{case.recall[1]:<10.2f} {case.recall[3]:<10.2f} "
                f"{case.mrr:<6.2f} {case.ndcg:<6.2f}"
            )
        lines.append("")
    else:
        lines.append(f"({len(report.cases)} cases; per-case rows are in --json)")
    layer_table = format_layers(report)
    if layer_table:
        lines.extend(layer_table)
        lines.append("")
    for name, value in report.metrics.items():
        limit = thresholds.get(name)
        marker = "" if limit is None or not isinstance(limit, (int, float)) else f"  (gate {limit})"
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
    lines = ["channel                recall@1  recall@3  recall@5   mrr  ndcg@5  stale  x-tenant"]
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


class _LocalSentenceTransformerEmbedder:
    """A local sentence-transformers model, imported only when it is asked for.

    Fine-tuning is not part of the shipped dependency set, so the import stays
    inside the constructor: a run that only measures the sparse channel never
    needs torch, and CI keeps working on the plain install.
    """

    def __init__(self, path: str, *, batch_size: int = 32) -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self._model = SentenceTransformer(path)
        self._batch_size = batch_size
        # A sweep asks the same query over and over, once per configuration;
        # the cache keeps that from turning into one forward pass per channel.
        self._cache: dict[str, list[float]] = {}

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        missing = [text for text in texts if text not in self._cache]
        if missing:
            vectors = self._model.encode(
                missing,
                batch_size=self._batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
            for text, row in zip(missing, vectors, strict=True):
                self._cache[text] = [float(value) for value in row]
        return [self._cache[text] for text in texts]


def _dense_provider(
    settings: Settings, local_model: str | None = None
) -> tuple[EmbeddingProvider | None, str]:
    """The dense provider for the evaluation, with a label for the report.

    ``None`` means the run cannot measure a dense channel at all: the shipped
    fused search needs a real embedding model, and a stand-in with no semantics
    would only produce a number that means nothing. Pass ``--local-embedding``
    or configure ``EMBEDDING_BASE_URL``.
    """
    if local_model:
        return _LocalSentenceTransformerEmbedder(local_model), f"local:{Path(local_model).name}"
    if settings.embedding_base_url and settings.embedding_model:
        api_key = (
            settings.embedding_api_key.get_secret_value() if settings.embedding_api_key else None
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
    return None, "none"


def _rewritten(search: PolicySearch, rewriter: QueryRewriter | None) -> PolicySearch:
    """Wrap the finished retriever so every channel is measured on the same text."""
    return search if rewriter is None else RewritingPolicySearch(search, rewriter)


def _sparse_factory(
    max_k: int, rewriter: QueryRewriter | None = None
) -> Callable[[Session], PolicySearch]:
    """The shipped sparse channel."""

    def factory(session: Session) -> PolicySearch:
        return _rewritten(PolicySearchService(session, max_results=max_k, min_score=0.01), rewriter)

    return factory


def _hybrid_factory(
    settings: Settings,
    provider: EmbeddingProvider,
    max_k: int,
    reranker: RerankClient | None = None,
    rewriter: QueryRewriter | None = None,
    candidate_pool: int | None = None,
) -> Callable[[Session], PolicySearch]:
    """Sparse plus dense, fused by per-channel z-score; optionally reranked.

    This is the shipped search. ``provider`` is required: the fused channel has
    no meaning without a real embedding model.
    """

    def factory(session: Session) -> PolicySearch:
        return _rewritten(
            HybridPolicySearch(
                session,
                sparse=PolicySearchService(session, max_results=max_k, min_score=0.01),
                embedder=provider,
                reranker=reranker,
                max_results=max_k,
                candidate_pool=candidate_pool or settings.retrieval_candidate_pool,
                dense_min_score=settings.retrieval_dense_min_score,
            ),
            rewriter,
        )

    return factory


def _run(
    dataset: dict[str, Any],
    database_url: str,
    *,
    k_values: tuple[int, ...] = DEFAULT_K,
    channels: tuple[str, ...] = ("bm25",),
    rewrite: bool = True,
    local_model: str | None = None,
    candidate_pool: int | None = None,
) -> tuple[list[Report], list[str]]:
    settings = Settings(
        environment="test",
        database_url=database_url,
        auto_create_tables=True,
    )
    # The evaluation uses the shipped rewriter defaults, so a local .env cannot
    # move the benchmark the comparison is made against.
    rewriter: QueryRewriter | None = QueryRewriter() if rewrite else None
    engine = build_engine(settings.database_url)
    create_all(engine)
    session_factory = build_session_factory(engine)
    seed_all(session_factory)
    skipped: list[str] = []
    reports: list[Report] = []
    try:
        max_k = max(k_values)
        provider, provider_label = _dense_provider(settings, local_model)
        if provider is not None:
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
                search_factory = _sparse_factory(max_k, rewriter)
                label = "bm25"
            elif channel == "hybrid":
                if provider is None:
                    # A fused channel without a real model is not a comparison,
                    # it is a different channel wearing the same name.
                    skipped.append(
                        "hybrid: no embedding model; pass --local-embedding "
                        "or set EMBEDDING_BASE_URL and EMBEDDING_MODEL"
                    )
                    continue
                search_factory = _hybrid_factory(
                    settings,
                    provider,
                    max_k,
                    rewriter=rewriter,
                    candidate_pool=candidate_pool,
                )
                label = f"hybrid({provider_label})"
            elif channel == "rerank":
                if provider is None:
                    skipped.append("rerank: no embedding model (see hybrid)")
                    continue
                reranker = build_reranker(settings)
                if reranker is None:
                    skipped.append("rerank: no RERANK_URL configured")
                    continue
                search_factory = _hybrid_factory(
                    settings, provider, max_k, reranker, rewriter=rewriter
                )
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


def _sweep(
    dataset: dict[str, Any],
    database_url: str,
    *,
    channels: tuple[str, ...],
    k_values: tuple[int, ...],
    pools: tuple[int, ...],
    rewrite: bool,
    local_model: str | None,
) -> str:
    """Scan the two depth knobs a retriever actually exposes.

    ``k`` is how many clauses the answer may cite; ``pool`` (top-n) is how many
    candidates a channel contributes to the fusion before the cut. Both are
    read from one ranking per configuration: the run retrieves ``max(k)`` and
    the per-case ``ndcg_by_k`` gives every intermediate cut for free.

    No gate is checked here: the sweep reports the curve, and the thresholds
    stay attached to the shipped configuration.
    """
    settings = Settings(
        environment="test",
        database_url=database_url,
        auto_create_tables=True,
    )
    rewriter: QueryRewriter | None = QueryRewriter() if rewrite else None
    engine = build_engine(settings.database_url)
    create_all(engine)
    session_factory = build_session_factory(engine)
    seed_all(session_factory)
    try:
        provider, provider_label = _dense_provider(settings, local_model)
        if provider is not None:
            with session_factory() as session:
                store_embeddings(session, provider)
                session.commit()
        elif any(channel != "bm25" for channel in channels):
            raise SystemExit(
                "a dense depth sweep needs an embedding model; pass "
                "--local-embedding <path> or set EMBEDDING_BASE_URL and "
                "EMBEDDING_MODEL"
            )
        short = provider_label.split(":")[-1][:16]
        # One pass per (channel, depth); every k and every layer is read from
        # the same ranking, so a sweep costs one evaluation per row group.
        results: list[tuple[str, Report, tuple[int, ...]]] = []
        for channel in channels:
            depths: tuple[int, ...] = pools if channel == "hybrid" else (max(k_values),)
            for depth in depths:
                k_subset = tuple(k for k in k_values if k <= depth)
                if not k_subset:
                    continue
                if channel == "bm25":
                    search_factory = _sparse_factory(depth, rewriter)
                    name = "bm25"
                elif channel == "hybrid":
                    if provider is None:
                        continue
                    search_factory = _hybrid_factory(
                        settings,
                        provider,
                        depth,
                        rewriter=rewriter,
                        candidate_pool=depth,
                    )
                    name = f"hybrid[{short}]"
                else:
                    continue
                report = evaluate(
                    dataset,
                    session_factory,
                    k_values=k_subset,
                    search_factory=search_factory,
                    channel=name,
                    depth=depth,
                )
                label = name if channel == "bm25" else f"{name} n={depth}"
                results.append((label, report, k_subset))
        lines: list[str] = []
        for layer in ("gold_test", "gold_dev", "all"):
            rows: list[str] = []
            for label, report, k_subset in results:
                metrics = report.metrics if layer == "all" else report.layers.get(layer, {})
                if not metrics:
                    continue
                cells = "".join(f"{metrics.get(f'recall@{k}', 0.0):<9.3f}" for k in k_values)
                ndcg = metrics.get(f"ndcg@{max(k_subset)}", 0.0)
                rows.append(f"{label:<30}{cells}{metrics.get('mrr', 0.0):<7.3f}{ndcg:<8.3f}")
            if not rows:
                continue
            head = (
                f"{'layer: ' + layer:<30}"
                + "".join(f"R@{k:<7}" for k in k_values)
                + "mrr    ndcg@maxk"
            )
            lines.append(head)
            lines.extend(rows)
            lines.append("")
        return chr(10).join(lines)
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
    parser.add_argument(
        "--rewrite",
        choices=("on", "off"),
        default="on",
        help="Run every channel through the query rewriter (default: on).",
    )
    parser.add_argument(
        "--local-embedding",
        help="Path to a local sentence-transformers model to embed with.",
    )
    parser.add_argument(
        "--candidate-pool",
        type=int,
        help="Top-n candidates each channel contributes before the cut.",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Scan top-k and top-n instead of gating one configuration.",
    )
    parser.add_argument(
        "--sweep-k",
        default="1,2,3,5,8,10,15",
        help="Comma separated k values for --sweep.",
    )
    parser.add_argument(
        "--sweep-pools",
        default="10,20,30,50",
        help="Comma separated candidate pools (top-n) for --sweep.",
    )
    args = parser.parse_args()

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    thresholds = dataset.get("thresholds", {})
    k_values = tuple(dataset.get("k_values", DEFAULT_K))
    channels = tuple(part.strip() for part in args.channels.split(",") if part.strip())
    if not channels:
        print("no channel selected")
        return 2

    if args.sweep:
        sweep_k = tuple(int(part) for part in args.sweep_k.split(",") if part.strip())
        sweep_pools = tuple(int(part) for part in args.sweep_pools.split(",") if part.strip())
        if args.database_url:
            print(
                _sweep(
                    dataset,
                    args.database_url,
                    channels=channels,
                    k_values=sweep_k,
                    pools=sweep_pools,
                    rewrite=args.rewrite == "on",
                    local_model=args.local_embedding,
                )
            )
        else:
            with tempfile.TemporaryDirectory() as directory:
                url = f"sqlite+pysqlite:///{Path(directory) / 'retrieval.db'}"
                print(
                    _sweep(
                        dataset,
                        url,
                        channels=channels,
                        k_values=sweep_k,
                        pools=sweep_pools,
                        rewrite=args.rewrite == "on",
                        local_model=args.local_embedding,
                    )
                )
        return 0

    if args.database_url:
        reports, skipped = _run(
            dataset,
            args.database_url,
            k_values=k_values,
            channels=channels,
            rewrite=args.rewrite == "on",
            local_model=args.local_embedding,
            candidate_pool=args.candidate_pool,
        )
    else:
        with tempfile.TemporaryDirectory() as directory:
            url = f"sqlite+pysqlite:///{Path(directory) / 'retrieval.db'}"
            reports, skipped = _run(
                dataset,
                url,
                k_values=k_values,
                channels=channels,
                rewrite=args.rewrite == "on",
                local_model=args.local_embedding,
                candidate_pool=args.candidate_pool,
            )

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
                            "layers": report.layers,
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
                            "cases": [
                                {
                                    "id": case.case_id,
                                    "layer": case.layer,
                                    "recall": {str(k): value for k, value in case.recall.items()},
                                    "mrr": case.mrr,
                                    "ndcg": case.ndcg,
                                    "retrieved": list(case.retrieved),
                                }
                                for case in report.cases
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
