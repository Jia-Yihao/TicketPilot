from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import Identity
from app.core.time import utcnow
from app.models.document import Document, DocumentChunk

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[a-zA-Z0-9_]+")


def tokenize(text: str) -> list[str]:
    """Zero-dependency tokenizer: ASCII words plus CJK unigrams and bigrams.

    Unigrams keep single-character hits reachable, bigrams carry the phrase
    discrimination Chinese needs without a dictionary, ASCII words stay whole.
    """
    tokens: list[str] = [match.lower() for match in _LATIN.findall(text)]
    for run in _CJK.findall(text):
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


# Closed vocabulary for "which scale is this score on". Defined here, next to
# the chunk types that carry it, and nowhere else in ``app/``.
SCORE_KINDS: tuple[str, ...] = ("sparse", "dense", "fusion", "rerank")

# Why a search came back empty. The three mean different things to the customer:
# nothing to search for, no usable clause in the library at all, or clauses that
# exist but did not match this phrasing.
EMPTY_REASONS: tuple[str, ...] = ("empty_query", "no_visible_chunks", "no_match")


def classify_empty(query: str, visible: int) -> str:
    """Name why a search returned nothing.

    ``visible`` is the number of chunks the identity may read right now. A blank
    query never looks at the library; a non-empty one with no visible chunk means
    the library has nothing to offer; everything else is a miss on this phrasing.
    """
    if not query.strip():
        return "empty_query"
    if visible <= 0:
        return "no_visible_chunks"
    return "no_match"


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_version: str
    title: str
    section: str
    source_uri: str
    content: str
    score: float
    effective_from: date
    effective_to: date | None
    # Appended last so the fields above keep their required-ness and every
    # existing construction site stays untouched.
    score_kind: str = "sparse"

    def citation(self) -> dict[str, Any]:
        return {
            "type": "policy",
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "version": self.document_version,
            "title": self.title,
            "section": self.section,
            "source_uri": self.source_uri,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "score": round(self.score, 6),
        }


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    """A chunk with its channel score, kept next to the row it came from."""

    chunk: DocumentChunk
    document: Document
    score: float
    score_kind: str = "sparse"

    def to_retrieved(self) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=self.chunk.id,
            document_id=self.document.id,
            document_version=self.document.version,
            title=self.document.title,
            section=self.chunk.section,
            source_uri=self.document.source_uri,
            content=self.chunk.content,
            score=self.score,
            effective_from=self.document.effective_from,
            effective_to=self.document.effective_to,
            score_kind=self.score_kind,
        )


class PolicySearch(Protocol):
    """Anything that can retrieve policy chunks for an identity."""

    def search(
        self,
        identity: Identity,
        query: str,
        *,
        today: date | None = None,
        limit: int | None = None,
    ) -> list[RetrievedChunk]: ...


class PolicySearchService:
    """Small deterministic BM25 baseline; pgvector can replace it behind this interface."""

    def __init__(self, session: Session, *, max_results: int = 3, min_score: float = 0.01) -> None:
        self.session = session
        self.max_results = max_results
        self.min_score = min_score

    def visible_chunks(
        self, identity: Identity, *, today: date | None = None
    ) -> list[tuple[DocumentChunk, Document]]:
        """Chunks the identity may read: same tenant, active, inside the effective window.

        The rows come back in a fixed order (``chunk id``), not in whatever
        order the database happens to return them. BM25 ties are broken by
        that order, so an unspecified order would make the ranking — and the
        candidate list handed to the reranker — differ between SQLite and
        PostgreSQL for the very same corpus.
        """
        today = today or utcnow().date()
        rows = self.session.execute(
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                Document.tenant_id == identity.tenant_id,
                Document.status == "ACTIVE",
                Document.effective_from <= today,
                (Document.effective_to.is_(None)) | (Document.effective_to >= today),
            )
            .order_by(DocumentChunk.id)
        ).all()
        return [(chunk, document) for chunk, document in rows]

    def ranked(
        self,
        identity: Identity,
        query: str,
        *,
        today: date | None = None,
        limit: int | None = None,
    ) -> list[ScoredChunk]:
        """Score the visible chunks with BM25 and return them best first."""
        if not query.strip():
            return []
        today = today or utcnow().date()
        rows = self.visible_chunks(identity, today=today)
        if not rows:
            return []

        corpus: list[tuple[DocumentChunk, Document, Counter[str], list[str]]] = []
        for chunk, document in rows:
            text = f"{document.title}\n{chunk.section}\n{chunk.content}"
            tokens = tokenize(text)
            corpus.append((chunk, document, Counter(tokens), tokens))

        query_terms = list(dict.fromkeys(tokenize(query)))
        if not query_terms:
            return []
        query_term_set = set(query_terms)
        total_docs = len(corpus)
        avg_len = sum(len(tokens) for _, _, _, tokens in corpus) / total_docs
        doc_freq = {
            term: sum(1 for _, _, _, tokens in corpus if term in tokens)
            for term in query_terms
        }
        k1, b = 1.5, 0.75

        scored: list[ScoredChunk] = []
        for chunk, document, counts, tokens in corpus:
            score = 0.0
            length = len(tokens)
            for term in query_terms:
                tf = counts.get(term, 0)
                if not tf:
                    continue
                df = doc_freq[term]
                idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
                score += idf * (tf * (k1 + 1)) / (
                    tf + k1 * (1 - b + b * length / max(avg_len, 1e-9))
                )
            # Heading and title matches are stronger evidence than body repetition.
            section_terms = set(tokenize(chunk.section))
            title_terms = set(tokenize(document.title))
            score += 0.8 * len(query_term_set & section_terms)
            score += 0.3 * len(query_term_set & title_terms)
            if score >= self.min_score:
                scored.append(ScoredChunk(chunk=chunk, document=document, score=score))

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[: (limit or self.max_results)]

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
        today = today or utcnow().date()
        return [
            scored.to_retrieved()
            for scored in self.ranked(identity, query, today=today, limit=limit)
        ]
