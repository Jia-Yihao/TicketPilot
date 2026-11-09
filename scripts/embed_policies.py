"""Build the dense policy index.

Embeds every policy chunk and stores the vectors in ``document_chunks.embedding``.

The provider is the one the deployment configures: ``EMBEDDING_BASE_URL`` and
``EMBEDDING_MODEL`` name an OpenAI-compatible ``/embeddings`` endpoint. Without
them there is nothing to build — the fused search has no meaning without a real
model, and writing vectors from a stand-in would only poison the index, so the
script refuses instead.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db.session import build_engine, build_session_factory, create_all
from app.models.document import DocumentChunk
from app.retrieval.dense import (
    EmbeddingProvider,
    OpenAICompatibleEmbedder,
    store_embeddings,
)


def _provider(settings: Settings) -> tuple[EmbeddingProvider, str]:
    """The configured embedding endpoint, or a refusal."""
    if not (settings.embedding_base_url and settings.embedding_model):
        raise SystemExit(
            "no embedding endpoint configured; set EMBEDDING_BASE_URL and "
            "EMBEDDING_MODEL (the index has to be built by the same model the "
            "search queries with)"
        )
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
        f"endpoint:{settings.embedding_model}",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the dense policy index.")
    parser.add_argument(
        "--database-url",
        help="Use this database instead of the configured one.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Re-embed the chunks that already carry a vector.",
    )
    args = parser.parse_args()

    settings = get_settings()
    database_url = args.database_url or settings.database_url
    engine = build_engine(database_url)
    if settings.auto_create_tables:
        create_all(engine)
    session_factory = build_session_factory(engine)
    provider, label = _provider(settings)

    written = 0
    try:
        with session_factory() as session:
            if args.rebuild:
                chunk_ids = list(session.scalars(select(DocumentChunk.id)))
                written = store_embeddings(session, provider, chunk_ids=chunk_ids)
            else:
                written = store_embeddings(session, provider)
            session.commit()
    except ValueError as exc:
        engine.dispose()
        raise SystemExit(f"dense index refused: {exc}") from exc
    engine.dispose()
    print(f"provider={label} dense index updated: {written} chunks embedded")


if __name__ == "__main__":
    main()
