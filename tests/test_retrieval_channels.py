from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.security import Identity
from app.db.session import build_engine, build_session_factory, create_all
from app.models.document import Document, DocumentChunk
from app.retrieval.build import build_policy_search
from app.retrieval.dense import (
    HashingEmbedder,
    cosine_similarity,
    index_dimensions,
    store_embeddings,
)
from app.retrieval.fusion import rrf_fuse, zscore_fuse
from app.retrieval.hybrid import DenseGate, HybridPolicySearch
from app.retrieval.rerank import RerankClient
from app.retrieval.rewrite import RewritingPolicySearch
from app.retrieval.search import PolicySearchService, tokenize
from app.seed import seed_all

ALICE = Identity(tenant_id="demo", user_id="alice", roles=frozenset())


class _BrokenEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding endpoint is down")


class _CountingEmbedder:
    def __init__(self, inner: HashingEmbedder) -> None:
        self.inner = inner
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return self.inner.embed(texts)


@pytest.fixture
def seeded(tmp_path: Path):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'channels.db'}")
    create_all(engine)
    session_factory = build_session_factory(engine)
    seed_all(session_factory)
    yield session_factory
    engine.dispose()


# ----------------------------------------------------------------- fusion
def test_rrf_fuse_adds_the_scores_of_every_channel() -> None:
    fused = rrf_fuse([["a", "b", "c"], ["c", "a"]], k=60)
    scores = dict(fused)

    assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["c"] == pytest.approx(1 / 63 + 1 / 61)
    assert fused[0][0] == "a"


def test_rrf_fuse_counts_a_repeated_id_once_per_channel() -> None:
    fused = rrf_fuse([["a", "a", "b"]])

    assert dict(fused)["a"] == pytest.approx(1 / 61)
    assert dict(fused)["b"] == pytest.approx(1 / 63)


def test_rrf_fuse_keeps_the_first_seen_order_on_ties() -> None:
    fused = rrf_fuse([["b", "a"], ["a", "b"]])

    assert [doc_id for doc_id, _ in fused] == ["b", "a"]


def test_rrf_fuse_truncates_to_top_n() -> None:
    fused = rrf_fuse([["a", "b", "c"]], top_n=2)

    assert [doc_id for doc_id, _ in fused] == ["a", "b"]


def test_zscore_fuse_ignores_a_channel_without_spread() -> None:
    fused = dict(zscore_fuse([[("a", 1.0), ("b", 1.0)], [("a", 5.0), ("b", 0.0)]], alpha=0.5))
    single = dict(zscore_fuse([[("a", 5.0), ("b", 0.0)]]))

    assert fused["a"] == pytest.approx(single["a"] * 0.5)
    assert fused["b"] == pytest.approx(single["b"] * 0.5)


def test_zscore_fuse_ranks_the_better_document_first() -> None:
    fused = zscore_fuse([[("a", 9.0), ("b", 1.0)], [("a", 1.0), ("b", 9.0)]])

    assert fused[0][0] == "a"


# -------------------------------------------------------------- tokenizer
def test_tokenizer_emits_unigrams_and_bigrams() -> None:
    tokens = tokenize("退款政策")

    assert "退" in tokens
    assert "退款" in tokens
    assert "政策" in tokens


def test_tokenizer_keeps_latin_words_whole() -> None:
    assert tokenize("ORD-1001") == ["ord", "1001"]


# ----------------------------------------------------------------- dense
def test_hashing_embedder_is_deterministic_and_normalised() -> None:
    embedder = HashingEmbedder(64)

    first = embedder.embed(["退款政策"])[0]
    second = embedder.embed(["退款政策"])[0]

    assert first == second
    assert sum(value * value for value in first) ** 0.5 == pytest.approx(1.0)


def test_hashing_embedder_ranks_similar_text_closer() -> None:
    embedder = HashingEmbedder(256)

    query, near, far = embedder.embed(["退款政策", "退款政策规定", "物流轨迹"])

    assert cosine_similarity(query, near) > cosine_similarity(query, far)


def test_store_embeddings_writes_the_vector_column(seeded) -> None:
    with seeded() as session:
        provider = HashingEmbedder(64)
        written = store_embeddings(session, provider)
        session.commit()
        stored = list(session.scalars(select(DocumentChunk.embedding)))

    assert written > 0
    assert len(stored) == written


def test_store_embeddings_refuses_a_second_embedding_space(seeded) -> None:
    """Mixed widths score 0 by cosine, so the write is refused instead."""
    with seeded() as session:
        chunk_ids = list(session.scalars(select(DocumentChunk.id)))
        assert len(chunk_ids) > 1
        store_embeddings(session, HashingEmbedder(64), chunk_ids=chunk_ids[:1])
        session.commit()

        with pytest.raises(ValueError, match="re-embed every chunk"):
            store_embeddings(session, HashingEmbedder(128), chunk_ids=chunk_ids[1:])

        # Moving the whole index to one space is what the refusal asks for.
        written = store_embeddings(session, HashingEmbedder(128), chunk_ids=chunk_ids)
        session.commit()

    assert written == len(chunk_ids)


def test_index_dimensions_reports_the_stored_widths(seeded) -> None:
    with seeded() as session:
        assert index_dimensions(session) == set()
        store_embeddings(session, HashingEmbedder(64))
        assert index_dimensions(session) == {64}


def test_hybrid_ignores_a_stale_embedding_space(seeded) -> None:
    """Vectors from another model score 0; they must not enter the fusion."""
    provider = HashingEmbedder(128)
    with seeded() as session:
        store_embeddings(session, HashingEmbedder(64))
        session.commit()
        sparse = PolicySearchService(session, max_results=3, min_score=0.01)
        hybrid = HybridPolicySearch(
            session,
            sparse=sparse,
            embedder=provider,
            max_results=3,
        )
        expected = [hit.chunk_id for hit in sparse.search(ALICE, "退款条件", limit=3)]
        hits = [hit.chunk_id for hit in hybrid.search(ALICE, "退款条件", limit=3)]

    assert hits == expected


# ---------------------------------------------------------------- rerank
def test_reranker_fails_open_on_an_unreachable_endpoint() -> None:
    client = RerankClient("http://127.0.0.1:9/rerank", "unit-test-model", timeout=0.5)

    assert client.rerank("退款", [("a", "文本")]) is None
    assert client.failures == 1


def test_reranker_opens_the_circuit_after_repeated_failures() -> None:
    client = RerankClient(
        "http://127.0.0.1:9/rerank",
        "unit-test-model",
        timeout=0.5,
        failure_threshold=2,
    )

    assert client.rerank("退款", [("a", "文本")]) is None
    assert client.rerank("退款", [("a", "文本")]) is None
    # The circuit is open now: the next call must not touch the network again.
    assert client.rerank("退款", [("a", "文本")]) is None
    assert client.failures == 2


def test_reranker_reorders_when_the_endpoint_answers(monkeypatch) -> None:
    def fake_post(url, *, json, headers, timeout):  # noqa: ANN001, ANN202
        assert url == "http://rerank.local/rerank"
        assert headers["Authorization"] == "Bearer unit-test-key"
        assert json["documents"] == ["第一段", "第二段"]
        return httpx.Response(
            200, json={"results": [{"index": 1, "relevance_score": 0.9}]}
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = RerankClient(
        "http://rerank.local/rerank", "unit-test-model", api_key="unit-test-key"
    )

    assert client.rerank("退款", [("a", "第一段"), ("b", "第二段")]) == [("b", 0.9)]
    assert client.failures == 0


# --------------------------------------------------------------- hybrid
def test_hybrid_falls_back_to_sparse_when_the_dense_channel_fails(seeded) -> None:
    with seeded() as session:
        sparse = PolicySearchService(session, max_results=3, min_score=0.01)
        hybrid = HybridPolicySearch(
            session,
            sparse=sparse,
            embedder=_BrokenEmbedder(),
            max_results=3,
        )
        sparse_hits = [
            hit.chunk_id for hit in sparse.search(ALICE, "退款条件是什么", limit=3)
        ]
        hybrid_hits = [
            hit.chunk_id for hit in hybrid.search(ALICE, "退款条件是什么", limit=3)
        ]

    assert hybrid_hits == sparse_hits


def test_hybrid_surfaces_a_chunk_only_the_dense_channel_found(seeded) -> None:
    provider = HashingEmbedder(128)
    with seeded() as session:
        store_embeddings(session, provider)
        session.commit()
        target = next(
            chunk
            for chunk in session.scalars(select(DocumentChunk))
            if "退货" in chunk.section or "退货" in chunk.content
        )
        vector = provider.embed([f"{target.section}\n{target.content}"])[0]

        class _FixedProvider:
            def embed(self, texts: list[str]) -> list[list[float]]:
                return [vector]

        # The sparse channel is silenced by an unreachable score, so only the
        # dense channel can bring the target chunk back.
        sparse = PolicySearchService(session, max_results=20, min_score=99.0)
        hybrid = HybridPolicySearch(
            session,
            sparse=sparse,
            embedder=_FixedProvider(),
            max_results=3,
        )
        hits = hybrid.search(ALICE, "完全无关的查询", limit=3)

    assert hits
    assert hits[0].chunk_id == target.id


def test_hybrid_never_returns_another_tenants_chunks(seeded) -> None:
    provider = HashingEmbedder(128)
    with seeded() as session:
        store_embeddings(session, provider)
        session.commit()
        hybrid = HybridPolicySearch(
            session,
            sparse=PolicySearchService(session, max_results=5, min_score=0.01),
            embedder=provider,
            max_results=5,
        )
        hits = hybrid.search(ALICE, "退款条件", limit=5)
        documents = {doc.id: doc for doc in session.scalars(select(Document))}

    assert hits
    assert all(documents[hit.document_id].tenant_id == "demo" for hit in hits)


# ------------------------------------------------------------ dense gate
def test_dense_gate_skips_the_embedder_when_bm25_is_confident(seeded) -> None:
    provider = _CountingEmbedder(HashingEmbedder(128))
    with seeded() as session:
        store_embeddings(session, provider)
        session.commit()
        provider.calls = 0
        sparse = PolicySearchService(session, max_results=3, min_score=0.01)
        hybrid = HybridPolicySearch(
            session,
            sparse=sparse,
            embedder=provider,
            max_results=3,
            dense_gate=DenseGate(),
        )

        # BM25 reads this question from its vocabulary and its top-1 section
        # covers the query terms, so the gate keeps the endpoint unused.
        hits = hybrid.search(ALICE, "退款申请需要谁审批", limit=3)
        expected = [
            hit.chunk_id for hit in sparse.search(ALICE, "退款申请需要谁审批", limit=3)
        ]

    assert hybrid.last_dense_decision == "sparse_confident"
    assert provider.calls == 0
    assert [hit.chunk_id for hit in hits] == expected


def test_dense_gate_opens_when_bm25_top1_does_not_cover_the_query(seeded) -> None:
    provider = _CountingEmbedder(HashingEmbedder(128))
    with seeded() as session:
        store_embeddings(session, provider)
        session.commit()
        provider.calls = 0
        hybrid = HybridPolicySearch(
            session,
            sparse=PolicySearchService(session, max_results=3, min_score=0.01),
            embedder=provider,
            max_results=3,
            dense_gate=DenseGate(),
        )

        # A labelled BM25 rank-1 miss: the query shares little with the chunk
        # BM25 ranks first, even though its score is high.
        hybrid.search(ALICE, "什么情况下要创建最高优先级的工单", limit=3)

    assert hybrid.last_dense_decision == "low_coverage"
    assert provider.calls == 1


def test_dense_gate_opens_when_sparse_finds_nothing(seeded) -> None:
    provider = _CountingEmbedder(HashingEmbedder(128))
    with seeded() as session:
        store_embeddings(session, provider)
        session.commit()
        provider.calls = 0
        hybrid = HybridPolicySearch(
            session,
            sparse=PolicySearchService(session, max_results=3, min_score=99.0),
            embedder=provider,
            max_results=3,
            dense_gate=DenseGate(),
        )

        hits = hybrid.search(ALICE, "zzzz", limit=3)

    assert hybrid.last_dense_decision == "no_sparse_hits"
    assert provider.calls == 1
    assert hits


def test_dense_gate_can_be_switched_off(seeded) -> None:
    provider = _CountingEmbedder(HashingEmbedder(128))
    with seeded() as session:
        store_embeddings(session, provider)
        session.commit()
        provider.calls = 0
        hybrid = HybridPolicySearch(
            session,
            sparse=PolicySearchService(session, max_results=3, min_score=0.01),
            embedder=provider,
            max_results=3,
            dense_gate=None,
        )

        hybrid.search(ALICE, "退款申请需要谁审批", limit=3)

    assert hybrid.last_dense_decision == "always_on"
    assert provider.calls == 1


def test_build_policy_search_passes_the_configured_dense_gate() -> None:
    settings = Settings(
        environment="test",
        retrieval_dense_enabled=True,
        embedding_base_url="http://127.0.0.1:1234/v1",
        embedding_model="text-embedding-bge-m3",
        retrieval_dense_gate_min_coverage=0.4,
    )

    engine = build_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    session_factory = build_session_factory(engine)
    try:
        with session_factory() as session:
            search = build_policy_search(session, settings)
            assert isinstance(search, RewritingPolicySearch)
            assert isinstance(search.inner, HybridPolicySearch)
            assert search.dense_gate == DenseGate(min_coverage=0.4)
    finally:
        engine.dispose()


def test_build_policy_search_can_restore_the_always_on_channel() -> None:
    settings = Settings(
        environment="test",
        retrieval_dense_enabled=True,
        retrieval_dense_gate_enabled=False,
        embedding_base_url="http://127.0.0.1:1234/v1",
        embedding_model="text-embedding-bge-m3",
    )

    engine = build_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    session_factory = build_session_factory(engine)
    try:
        with session_factory() as session:
            search = build_policy_search(session, settings)
            assert isinstance(search, RewritingPolicySearch)
            assert isinstance(search.inner, HybridPolicySearch)
            assert search.dense_gate is None
    finally:
        engine.dispose()
