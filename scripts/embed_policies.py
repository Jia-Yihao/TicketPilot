"""Build the dense policy index.

Embeds every policy chunk and stores the vectors in ``document_chunks.embedding``.

The provider follows the deployment settings: with ``EMBEDDING_BASE_URL`` and
``EMBEDDING_MODEL`` set it calls that OpenAI-compatible endpoint, otherwise it
falls back to the offline hashing stand-in so the index can be built without
any external service.

The stand-in carries no semantics, so a production database refuses it here
just as the app refuses to start with dense retrieval enabled and no endpoint.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import build_engine, build_session_factory, create_all
from app.models.document import DocumentChunk
from app.retrieval.dense import (
    EmbeddingProvider,
    HashingEmbedder,
    OpenAICompatibleEmbedder,
    store_embeddings,
)


def _provider(settings, *, offline: bool) -> tuple[EmbeddingProvider, str]:
    """The configured endpoint, or the offline stand-in when there is none.

    The stand-in writes character-hash vectors with no semantics, so a
    production deployment must not build its index with it: production
    refuses instead, the same way the app refuses to start with dense
    retrieval enabled and no endpoint.
    """
    if not offline and settings.embedding_base_url and settings.embedding_model:
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
    if settings.environment == "prod":
        raise SystemExit(
            "refusing to build the dense index with the offline hashing "
            "stand-in in production; set EMBEDDING_BASE_URL and EMBEDDING_MODEL"
        )
    return HashingEmbedder(settings.embedding_dimensions), "hashing(offline stand-in)"


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
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Force the offline hashing stand-in instead of the configured endpoint.",
    )
    args = parser.parse_args()

    settings = get_settings()
    database_url = args.database_url or settings.database_url
    engine = build_engine(database_url)
    if settings.auto_create_tables:
        create_all(engine)
    session_factory = build_session_factory(engine)
    provider, label = _provider(settings, offline=args.offline)

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
