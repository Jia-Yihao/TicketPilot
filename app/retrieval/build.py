"""Builds the policy search chain from the deployment settings.

One chain ships: sparse BM25 fused with a dense channel by per-channel
z-scores. The dense half needs an embedding endpoint; without one the chain
degrades to sparse only, which is the offline/dev path rather than a second
scheme. The reranker is orthogonal and still opt-in. The query rewriter wraps
the finished chain instead of being one of its channels.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.retrieval.dense import EmbeddingProvider, OpenAICompatibleEmbedder
from app.retrieval.hybrid import HybridPolicySearch
from app.retrieval.rerank import RerankClient
from app.retrieval.rewrite import QueryRewriter, RewritingPolicySearch
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
    """The dense provider, or ``None`` when no endpoint is configured.

    ``None`` means the chain runs sparse only. There is deliberately no hashing
    stand-in here: on this corpus it ranks worse than BM25, so silently
    swapping it in would be a quality trap rather than a fallback.
    """
    if not (settings.embedding_base_url and settings.embedding_model):
        return None
    return OpenAICompatibleEmbedder(
        settings.embedding_base_url,
        settings.embedding_model,
        api_key=_secret(settings.embedding_api_key),
        timeout=settings.embedding_timeout_seconds,
    )


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


def build_rewriter(settings: Settings) -> QueryRewriter | None:
    """The query rewriter, or ``None`` when the master switch is off."""
    if not settings.retrieval_query_rewrite_enabled:
        return None
    return QueryRewriter(
        numbers=settings.retrieval_query_rewrite_numbers,
        synonyms=settings.retrieval_query_rewrite_synonyms,
        sections=settings.retrieval_query_rewrite_sections,
        max_section_hints=settings.retrieval_query_rewrite_max_section_hints,
        max_expansions=settings.retrieval_query_rewrite_max_expansions,
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
    search: PolicySearch
    if embedder is None and reranker is None:
        search = sparse
    else:
        search = HybridPolicySearch(
            session,
            sparse=sparse,
            embedder=embedder,
            reranker=reranker,
            max_results=results,
            candidate_pool=settings.retrieval_candidate_pool,
            dense_min_score=settings.retrieval_dense_min_score,
        )
    # The rewriter wraps the finished chain: the dense channel and the reranker
    # must see the same rewritten text the sparse channel scored.
    rewriter = build_rewriter(settings)
    if rewriter is None:
        return search
    return RewritingPolicySearch(search, rewriter)
