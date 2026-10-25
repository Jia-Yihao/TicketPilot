"""Hybrid policy search: sparse BM25 plus an optional dense channel.

The two channels are fused with RRF (reciprocal rank fusion) so a chunk that
only one channel found can still reach the top. The optional reranker runs on
the fused candidates and fails open, so the retrieval chain keeps working when
the reranker is unreachable.

The hybrid path is opt-in: without a dense provider and stored embeddings the
service degrades to the sparse channel, which is the default deployment.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.core.security import Identity
from app.models.document import Document, DocumentChunk
from app.retrieval.dense import (
    EmbeddingProvider,
    chunk_text,
    cosine_similarity,
)
from app.retrieval.fusion import rrf_fuse
from app.retrieval.rerank import RerankClient
from app.retrieval.search import PolicySearchService, RetrievedChunk, ScoredChunk


class HybridPolicySearch:
    """Fuse the sparse and dense channels, then optionally rerank the result."""

    def __init__(
        self,
        session: Session,
        *,
        sparse: PolicySearchService,
        embedder: EmbeddingProvider | None = None,
        reranker: RerankClient | None = None,
        rrf_k: int = 60,
        max_results: int = 3,
        candidate_pool: int = 20,
        dense_min_score: float = 0.0,
    ) -> None:
        self.session = session
        self.sparse = sparse
        self.embedder = embedder
        self.reranker = reranker
        self.rrf_k = rrf_k
        self.max_results = max_results
        self.candidate_pool = max(max_results, candidate_pool)
        self.dense_min_score = dense_min_score

    def search(
        self,
        identity: Identity,
        query: str,
        *,
        today: date | None = None,
        limit: int | None = None,
    ) -> list[RetrievedChunk]:
        if not query.strip():
            return []
        sparse_hits = self.sparse.ranked(
            identity, query, today=today, limit=self.candidate_pool
        )
        dense_hits = self._dense_ranked(identity, query, today=today)
        if not sparse_hits and not dense_hits:
            return []

        by_id = {hit.chunk.id: hit for hit in sparse_hits}
        channels: list[list[str]] = []
        if sparse_hits:
            channels.append([hit.chunk.id for hit in sparse_hits])
        for hit in dense_hits:
            by_id.setdefault(hit.chunk.id, hit)
        if dense_hits:
            channels.append([hit.chunk.id for hit in dense_hits])

        fused = rrf_fuse(channels, k=self.rrf_k)
        order = [chunk_id for chunk_id, _ in fused]
        if self.reranker is not None:
            order = self._rerank(query, order, by_id)
        top = order[: (limit or self.max_results)]
        return [by_id[chunk_id].to_retrieved() for chunk_id in top if chunk_id in by_id]

    # ------------------------------------------------------------- channels
    def _dense_ranked(
        self, identity: Identity, query: str, *, today: date | None
    ) -> list[ScoredChunk]:
        if self.embedder is None:
            return []
        rows = self.sparse.visible_chunks(identity, today=today)
        candidates: list[tuple[DocumentChunk, Document, list[float]]] = [
            (chunk, document, chunk.embedding)
            for chunk, document in rows
            if chunk.embedding
        ]
        if not candidates:
            return []
        try:
            vectors = self.embedder.embed([query])
        except Exception:
            # Fail open: a broken embedding endpoint must not break retrieval.
            return []
        if not vectors:
            return []
        query_vector = vectors[0]
        if not query_vector:
            return []
        scored: list[tuple[DocumentChunk, Document, float]] = []
        for chunk, document, embedding in candidates:
            # A vector from another model space scores 0 by cosine, so it is
            # dropped rather than ranked: an all-zero channel would add noise
            # to the fusion instead of evidence.
            if len(embedding) != len(query_vector):
                continue
            score = cosine_similarity(query_vector, embedding)
            if score > 0.0 and score >= self.dense_min_score:
                scored.append((chunk, document, score))
        scored.sort(key=lambda item: -item[2])
        return [
            ScoredChunk(chunk=chunk, document=document, score=score)
            for chunk, document, score in scored[: self.candidate_pool]
        ]

    def _rerank(
        self,
        query: str,
        order: list[str],
        by_id: dict[str, ScoredChunk],
    ) -> list[str]:
        documents = [
            (chunk_id, chunk_text(by_id[chunk_id].chunk)) for chunk_id in order
        ]
        reranked = self.reranker.rerank(query, documents) if self.reranker else None
        if not reranked:
            return order
        scores = dict(reranked)
        reranked_ids = [chunk_id for chunk_id, _ in reranked if chunk_id in scores]
        remaining = [chunk_id for chunk_id in order if chunk_id not in set(reranked_ids)]
        return reranked_ids + remaining
