"""Hybrid policy search: sparse BM25 plus an optional dense channel.

The two channels are fused with per-channel z-scores, so a chunk that only one
channel found can still reach the top and the sharp head of the BM25 list
survives the merge. The optional reranker runs on the fused candidates and
fails open, so the retrieval chain keeps working when the reranker is
unreachable.

Fusion and evidence are deliberately two different numbers: a z-score is
normalized inside one query, so it ranks well and cannot say whether anything
was found at all. Every hit carries its channel score next to the fused one,
and the escalation gate reads that channel score.

The dense channel runs on every query whenever an embedder is configured.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date

from sqlalchemy.orm import Session

from app.core.security import Identity
from app.models.document import Document, DocumentChunk
from app.retrieval.dense import (
    EmbeddingProvider,
    chunk_text,
    cosine_similarity,
)
from app.retrieval.fusion import zscore_fuse
from app.retrieval.rerank import RerankClient
from app.retrieval.search import (
    PolicySearchService,
    RetrievedChunk,
    ScoredChunk,
)

# The sparse channel's weight when both channels answer. Measured on the
# layered set: 0.3 beat 0.5 by 2.0 points of recall@1 and 1.0 point of MRR,
# because the dense channel is the tie-breaker here, not the primary reader.
FUSION_ALPHA = 0.3


class HybridPolicySearch:
    """Fuse the sparse and dense channels, then optionally rerank the result."""

    def __init__(
        self,
        session: Session,
        *,
        sparse: PolicySearchService,
        embedder: EmbeddingProvider | None = None,
        reranker: RerankClient | None = None,
        max_results: int = 3,
        candidate_pool: int = 20,
        dense_min_score: float = 0.0,
    ) -> None:
        self.session = session
        self.sparse = sparse
        self.embedder = embedder
        self.reranker = reranker
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
        dense_hits: list[ScoredChunk] = (
            [] if self.embedder is None else self._dense_ranked(identity, query, today=today)
        )
        if not sparse_hits and not dense_hits:
            return []

        by_id = {hit.chunk.id: hit for hit in sparse_hits}
        sparse_scores = {hit.chunk.id: hit.score for hit in sparse_hits}
        channels: list[list[tuple[str, float]]] = []
        if sparse_hits:
            channels.append([(hit.chunk.id, hit.score) for hit in sparse_hits])
        for hit in dense_hits:
            by_id.setdefault(hit.chunk.id, hit)
        if dense_hits:
            channels.append([(hit.chunk.id, hit.score) for hit in dense_hits])

        if len(channels) == 1:
            # One channel answered: keep its own score. Re-normalizing a lone
            # list would change the number the caller sees without changing
            # the order, and the score would stop being readable.
            fused = channels[0]
            fused_kind = "sparse" if sparse_hits else "dense"
        else:
            fused = zscore_fuse(channels, alpha=FUSION_ALPHA)
            fused_kind = "fusion"
        candidate_order = [chunk_id for chunk_id, _ in fused]
        reranked: list[tuple[str, float]] = []
        if self.reranker is not None:
            reranked = self._rerank(query, candidate_order, by_id)
        top = self._scored_order(fused, reranked, fused_kind)[
            : (limit or self.max_results)
        ]
        return [
            self._to_retrieved(chunk_id, score, kind, by_id, sparse_scores)
            for chunk_id, score, kind in top
            if chunk_id in by_id
        ]

    @staticmethod
    def _to_retrieved(
        chunk_id: str,
        score: float,
        kind: str,
        by_id: dict[str, ScoredChunk],
        sparse_scores: dict[str, float],
    ) -> RetrievedChunk:
        """One hit with the ranking score and the evidence score side by side."""
        evidence_score, evidence_kind = HybridPolicySearch._evidence(
            chunk_id, by_id, sparse_scores
        )
        return replace(
            by_id[chunk_id].to_retrieved(),
            score=score,
            score_kind=kind,
            evidence_score=evidence_score,
            evidence_kind=evidence_kind,
        )

    @staticmethod
    def _scored_order(
        fused: list[tuple[str, float]],
        reranked: Sequence[tuple[str, float]],
        fused_kind: str,
    ) -> list[tuple[str, float, str]]:
        """Return ``(chunk_id, score, score_kind)`` in one scale per list.

        The order comes from the fusion (or the reranker), so the number next to
        it has to come from the same place: reporting a raw per-channel score
        here would let the score and the rank disagree. Without a rerank answer
        every row carries the fused score, tagged with the ruler that produced
        it. With one, the answered ids carry the endpoint score and the tail —
        the candidates the endpoint did not answer for — keeps its fused score
        clamped below the weakest endpoint score, so the list stays sorted and
        never mixes two rulers.
        """
        if not reranked:
            return [(chunk_id, score, fused_kind) for chunk_id, score in fused]
        floor = min(score for _, score in reranked)
        answered = {chunk_id for chunk_id, _ in reranked}
        # The endpoint's order is not guaranteed to follow its own scores, so
        # sort by score and keep the endpoint order on ties.
        head = sorted(enumerate(reranked), key=lambda item: (-item[1][1], item[0]))
        scored: list[tuple[str, float, str]] = [
            (chunk_id, score, "rerank") for _, (chunk_id, score) in head
        ]
        scored.extend(
            (chunk_id, min(score, floor), "rerank")
            for chunk_id, score in fused
            if chunk_id not in answered
        )
        return scored

    @staticmethod
    def _evidence(
        chunk_id: str,
        by_id: dict[str, ScoredChunk],
        sparse_scores: dict[str, float],
    ) -> tuple[float, str]:
        """The channel score the escalation gate reads for this chunk.

        Prefers the sparse score whenever BM25 saw the chunk: it is the only
        absolute scale in the chain, and on the layered set it separated
        off-domain messages from answerable ones far better than the fused
        z-score did. A chunk the dense channel alone found falls back to the
        cosine, which has its own threshold.
        """
        if chunk_id in sparse_scores:
            return sparse_scores[chunk_id], "sparse"
        hit = by_id[chunk_id]
        return hit.score, hit.score_kind

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
            ScoredChunk(chunk=chunk, document=document, score=score, score_kind="dense")
            for chunk, document, score in scored[: self.candidate_pool]
        ]

    def _rerank(
        self,
        query: str,
        order: list[str],
        by_id: dict[str, ScoredChunk],
    ) -> list[tuple[str, float]]:
        """Ask the endpoint for ``(chunk_id, score)`` pairs; empty means no opinion."""
        documents = [
            (chunk_id, chunk_text(by_id[chunk_id].chunk)) for chunk_id in order
        ]
        reranked = self.reranker.rerank(query, documents) if self.reranker else None
        if not reranked:
            return []
        return [
            (chunk_id, score) for chunk_id, score in reranked if chunk_id in by_id
        ]
