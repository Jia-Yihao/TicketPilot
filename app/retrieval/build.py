"""Builds the policy search chain from the deployment settings.

The sparse BM25 channel is always present. The dense and rerank channels are
opt-in: without them the service stays exactly the deterministic BM25 baseline
that the offline evaluation measures.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.retrieval.dense import (
    EmbeddingProvider,
    HashingEmbedder,
    OpenAICompatibleEmbedder,
)
from app.retrieval.hybrid import DenseGate, HybridPolicySearch
from app.retrieval.rerank import RerankClient
from app.retrieval.search import PolicySearch, PolicySearchService


def _secret(value: object) -> str | None:
    if value is None:
        return None
    getter = getattr(value, "get_secret_value", None)
    if getter is None:
        return None
    secret: str = getter()
    return secret or None


def build_embedder(settings: Settings) -> EmbeddingProvider | None:
    """The dense provider, or ``None`` when the dense channel is off."""
    if not settings.retrieval_dense_enabled:
        return None
    if settings.embedding_base_url and settings.embedding_model:
        return OpenAICompatibleEmbedder(
            settings.embedding_base_url,
            settings.embedding_model,
            api_key=_secret(settings.embedding_api_key),
            timeout=settings.embedding_timeout_seconds,
        )
    return HashingEmbedder(settings.embedding_dimensions)


def build_reranker(settings: Settings) -> RerankClient | None:
    """The reranker, or ``None`` when the rerank channel is off."""
    if not settings.retrieval_rerank_enabled or not settings.rerank_url:
        return None
    return RerankClient(
        settings.rerank_url,
        settings.rerank_model,
        api_key=_secret(settings.rerank_api_key),
        timeout=settings.rerank_timeout_seconds,
        top_n=settings.rerank_top_n,
        failure_threshold=settings.rerank_failure_threshold,
        cooldown_seconds=settings.rerank_cooldown_seconds,
    )


def build_policy_search(
    session: Session,
    settings: Settings,
    *,
    max_results: int | None = None,
    min_score: float | None = None,
) -> PolicySearch:
    """The policy search chain this deployment is configured for."""
    results = max_results or settings.policy_max_results
    sparse = PolicySearchService(
        session,
        max_results=results,
        min_score=settings.policy_min_score if min_score is None else min_score,
    )
    embedder = build_embedder(settings)
    reranker = build_reranker(settings)
    if embedder is None and reranker is None:
        return sparse
    dense_gate = (
        DenseGate(
            min_top1_score=settings.retrieval_dense_gate_min_top1_score,
            min_coverage=settings.retrieval_dense_gate_min_coverage,
        )
        if settings.retrieval_dense_gate_enabled
        else None
    )
    return HybridPolicySearch(
        session,
        sparse=sparse,
        embedder=embedder,
        reranker=reranker,
        rrf_k=settings.retrieval_rrf_k,
        max_results=results,
        candidate_pool=settings.retrieval_candidate_pool,
        dense_min_score=settings.retrieval_dense_min_score,
        dense_gate=dense_gate,
    )
