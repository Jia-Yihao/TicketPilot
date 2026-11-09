"""Dense retrieval channel.

Vectors live in the existing ``document_chunks.embedding`` column, so this
channel needs no extra service and no schema change.

One provider ships here: ``OpenAICompatibleEmbedder``, which talks to an
OpenAI-compatible ``/embeddings`` endpoint. The fused search is the shipped
scheme and it has no meaning without a real model, so a deployment without
``EMBEDDING_BASE_URL`` runs the sparse channel alone rather than a stand-in
that would only produce numbers.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import DocumentChunk


class EmbeddingProvider(Protocol):
    """Turns text into vectors."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity of two vectors; 0.0 when either side is empty."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


class OpenAICompatibleEmbedder:
    """Calls an OpenAI-compatible ``/embeddings`` endpoint."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        response = httpx.post(
            f"{self._base_url}/embeddings",
            json={"model": self._model, "input": list(texts)},
            headers=headers,
            timeout=self._timeout,
        )
        if response.is_error:
            raise RuntimeError(f"embedding endpoint returned HTTP {response.status_code}")
        payload = response.json()
        data = payload.get("data") or []
        vectors = [[float(value) for value in item["embedding"]] for item in data]
        if len(vectors) != len(texts):
            raise ValueError(
                f"embedding endpoint returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        return vectors


def chunk_text(chunk: DocumentChunk) -> str:
    """The text a chunk is embedded and reranked by."""
    return f"{chunk.section}\n{chunk.content}".strip()


def index_dimensions(session: Session, *, exclude_ids: Sequence[str] = ()) -> set[int]:
    """The vector widths the index already carries, outside the given ids."""
    statement = select(DocumentChunk.id, DocumentChunk.embedding)
    if exclude_ids:
        statement = statement.where(DocumentChunk.id.notin_(list(exclude_ids)))
    return {len(embedding) for _, embedding in session.execute(statement) if embedding}


def store_embeddings(
    session: Session,
    provider: EmbeddingProvider,
    *,
    chunk_ids: Sequence[str] | None = None,
) -> int:
    """Embed the given chunks and persist the vectors; returns the number written.

    Chunks that already carry a vector are skipped unless their id is listed
    explicitly, so re-running the indexer does not spend embeddings twice.

    One index is one vector space: cosine of two widths is zero, so a chunk
    embedded by another model would rank last forever without an error. The
    write is refused when the chunks it leaves behind use a different width.
    """
    statement = select(DocumentChunk).order_by(DocumentChunk.id)
    if chunk_ids is not None:
        statement = statement.where(DocumentChunk.id.in_(list(chunk_ids)))
    chunks = list(session.scalars(statement))
    pending = [chunk for chunk in chunks if not chunk.embedding or chunk.id in set(chunk_ids or ())]
    if not pending:
        return 0
    vectors = provider.embed([chunk_text(chunk) for chunk in pending])
    if len(vectors) != len(pending):
        raise ValueError(
            f"embedding provider returned {len(vectors)} vectors for {len(pending)} chunks"
        )
    widths = {len(vector) for vector in vectors if vector}
    if len(widths) > 1:
        raise ValueError(f"embedding provider returned mixed dimensions: {sorted(widths)}")
    kept = index_dimensions(session, exclude_ids=[chunk.id for chunk in pending])
    if kept and kept != widths:
        raise ValueError(
            f"index keeps {'/'.join(sorted(str(width) for width in kept))}-dimensional "
            f"vectors but this provider writes {sorted(widths)[0]}; re-embed every "
            "chunk with --rebuild before switching embedding models"
        )
    for chunk, vector in zip(pending, vectors, strict=True):
        chunk.embedding = [float(value) for value in vector]
    session.flush()
    return len(pending)
