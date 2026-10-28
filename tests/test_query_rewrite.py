from __future__ import annotations

from datetime import date

import pytest

from app.core.config import Settings
from app.core.security import Identity
from app.db.session import build_engine, build_session_factory, create_all
from app.retrieval.build import build_policy_search, build_rewriter
from app.retrieval.hybrid import HybridPolicySearch
from app.retrieval.rewrite import (
    FACET_ORDER,
    SECTION_TITLES,
    QueryRewriter,
    RewritingPolicySearch,
    detect_domain,
    detect_facets,
    parse_chinese_number,
)
from app.retrieval.search import PolicySearchService, RetrievedChunk

ALICE = Identity(tenant_id="demo", user_id="alice", roles=frozenset())
ALL_SECTIONS = {title for titles in SECTION_TITLES.values() for title in titles}


def _chunk(chunk_id: str = "chk_1", section: str = "退款条件") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc_1",
        document_version="v3",
        title="退款政策",
        section=section,
        source_uri="policies/refund-v3.md",
        content="已签收订单应在签收后 30 天内提出退款申请。",
        score=1.0,
        effective_from=date(2025, 1, 1),
        effective_to=None,
    )


class _RecordingSearch:
    """A PolicySearch that records the query it was handed."""

    def __init__(self, *, dense_decision: str = "sparse_confident") -> None:
        self.queries: list[str] = []
        self.last_dense_decision = dense_decision

    def search(
        self,
        identity: Identity,
        query: str,
        *,
        today: date | None = None,
        limit: int | None = None,
    ) -> list[RetrievedChunk]:
        self.queries.append(query)
        return [_chunk()]


class _PlainSearch:
    """A PolicySearch with no channel diagnostics at all (the sparse baseline)."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(
        self,
        identity: Identity,
        query: str,
        *,
        today: date | None = None,
        limit: int | None = None,
    ) -> list[RetrievedChunk]:
        self.queries.append(query)
        return [_chunk()]


@pytest.fixture
def factory():  # noqa: ANN201
    engine = build_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    yield build_session_factory(engine)
    engine.dispose()


# ------------------------------------------------------------ search text
def test_search_text_keeps_the_original_query() -> None:
    query = "签收以后多少天内可以申请退款"
    result = QueryRewriter().rewrite(query)

    assert result.original == query
    assert result.search_text.startswith(query)
    assert result.search_text == " ".join((query, *result.added_terms))


def test_rewrite_is_deterministic_across_instances() -> None:
    query = "严重延迟一百二十九元怎么处理"

    first = QueryRewriter().rewrite(query)
    second = QueryRewriter().rewrite(query)

    assert first == second


def test_empty_query_adds_nothing() -> None:
    result = QueryRewriter().rewrite("   ")

    assert result.search_text == "   "
    assert result.added_terms == ()
    assert result.facets == ()


# --------------------------------------------------------------- numbers
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("退款一百二十九元可以吗", "129元"),
        ("三十天内提出", "30天"),
        ("七天无理由", "7天"),
        ("两周内发货", "14天"),
        ("半个月到账", "15天"),
        ("一个月内处理", "30天"),
        ("四十八小时未更新", "48小时"),
        ("一千零五十元", "1050元"),
        ("全角１２９元", "129元"),
        ("一个工作日内答复", "1个工作日"),
    ],
)
def test_chinese_numbers_are_appended_in_arabic(query: str, expected: str) -> None:
    result = QueryRewriter().rewrite(query)

    assert expected in result.numbers
    assert expected in result.search_text
    # The Chinese form stays: 七天无理由 must still match its section title.
    assert result.search_text.startswith(query)


def test_chinese_numerals_are_parsed_with_units_and_zero() -> None:
    assert parse_chinese_number("一百二十九") == 129
    assert parse_chinese_number("一千零五十") == 1050
    assert parse_chinese_number("十五") == 15
    assert parse_chinese_number("两") == 2
    assert parse_chinese_number("十") == 10


def test_order_identifiers_are_never_touched() -> None:
    result = QueryRewriter().rewrite("订单 ORD-DEMO-1001 的运单 DE20010003 到哪了")

    assert result.numbers == ()
    assert "ORD-DEMO-1001" in result.search_text
    assert "DE20010003" in result.search_text


@pytest.mark.parametrize("query", ["一般规定", "一致同意", "一并寄回", "一部分费用"])
def test_non_numeric_words_are_not_converted(query: str) -> None:
    """The quantifier whitelist, not the numeral alone, decides a match."""
    assert QueryRewriter().rewrite(query).numbers == ()


def test_an_existing_arabic_form_is_not_appended_again() -> None:
    assert QueryRewriter().rewrite("七天内退款，7天内到账").numbers == ()


def test_numbers_can_be_switched_off() -> None:
    result = QueryRewriter(numbers=False).rewrite("三十天内退款")

    assert result.numbers == ()
    assert "30天" not in result.search_text


# -------------------------------------------------------------- synonyms
def test_synonyms_are_appended_and_the_original_is_kept() -> None:
    result = QueryRewriter().rewrite("快递一直不动")

    assert "物流" in result.synonyms
    # The colloquial wording stays; only the policy side is appended.
    assert result.search_text.startswith("快递一直不动")
    assert "物流" in result.search_text


def test_synonym_group_is_capped_at_four_additions() -> None:
    result = QueryRewriter().rewrite(
        "物流延迟赔偿运费签收优先级工单转人工审批到账期限余额证据"
    )

    assert len(result.synonyms) <= 4 * 15
    assert len(result.added_terms) <= 8


def test_synonyms_can_be_switched_off() -> None:
    result = QueryRewriter(synonyms=False).rewrite("退款怎么办")

    assert result.synonyms == ()


# ------------------------------------------------------------ expansions
def test_added_terms_never_exceed_the_budget() -> None:
    result = QueryRewriter(max_expansions=2).rewrite(
        "三十天内退款一百二十九元，物流什么时候到"
    )

    assert len(result.added_terms) == 2
    # Numbers are kept first, sections second, synonyms last.
    assert result.added_terms == ("30天", "129元")


def test_sections_win_over_synonyms_when_the_budget_runs_out() -> None:
    result = QueryRewriter(max_expansions=3).rewrite("三十天以内退钱")

    assert result.added_terms == ("30天", "退款条件", "退款")


# ---------------------------------------------------------------- facets
@pytest.mark.parametrize(
    ("query", "facet"),
    [
        ("退款申请需要谁审批", "WHO"),
        ("哪些商品不可以退货", "WHAT"),
        ("延迟多久算严重", "WHEN"),
        ("物流在哪个页面查", "WHERE"),
        ("为什么判定为延迟", "WHY"),
        ("延迟应该怎么处理", "HOW"),
    ],
)
def test_facets_are_detected_and_ordered(query: str, facet: str) -> None:
    facets = detect_facets(query)

    assert facet in facets
    assert list(facets) == [name for name in FACET_ORDER if name in facets]


def test_facets_do_not_invent_a_reading() -> None:
    assert detect_facets("ORD-DEMO-1001") == ()


# -------------------------------------------------------------- sections
@pytest.mark.parametrize(
    ("query", "section"),
    [
        ("退款政策规定哪些订单可以退款", "适用范围"),
        ("签收以后多少天内可以申请退款", "退款条件"),
        ("退款申请需要谁审批", "审批要求"),
        ("退款接口返回成功是不是就代表钱已经到账", "结果确认"),
        ("物流轨迹停在同一个节点需要联系承运商吗", "延迟判定"),
        ("严重延迟应该怎么处理", "处理方式"),
        ("回答物流问题需要哪些证据", "证据要求"),
        ("无理由退货的时间是多久", "七天无理由退货"),
        ("退货和退款到底有什么区别", "退货与退款的区别"),
        ("哪些商品是不可以退货的", "不予退货的情形"),
        ("已经同意退货能不能告诉用户已经退款了", "操作要求"),
        ("什么情况下要创建最高优先级的工单", "优先级定义"),
        ("重复提交工单会生成好几张工单吗", "创建规则"),
    ],
)
def test_section_keyword_rules(query: str, section: str) -> None:
    assert section in QueryRewriter().rewrite(query).section_hints


@pytest.mark.parametrize(
    ("query", "section"),
    [
        ("退款之后会怎样", "退款条件"),  # REFUND + WHEN
        ("为何物流延迟", "延迟判定"),  # DELAY + WHY
        ("退货何时算超时", "七天无理由退货"),  # RETURN + WHEN
        ("工单要满足什么要求", "优先级定义"),  # TICKET + WHAT
        ("工单该由谁跟进", "转人工"),  # TICKET + WHO
        ("延迟要怎么弄", "处理方式"),  # DELAY + HOW
    ],
)
def test_section_fallback_uses_the_facets(query: str, section: str) -> None:
    result = QueryRewriter().rewrite(query)

    assert result.facets
    assert result.section_hints[0] == section


def test_hints_are_limited_and_only_name_active_sections() -> None:
    result = QueryRewriter(max_section_hints=1).rewrite(
        "什么情况下要创建最高优先级的工单"
    )

    assert len(result.section_hints) == 1
    hints = set(QueryRewriter().rewrite("什么情况下要创建最高优先级的工单").section_hints)
    assert hints <= ALL_SECTIONS


def test_a_hint_already_in_the_query_is_skipped() -> None:
    result = QueryRewriter().rewrite("退款金额应该怎么计算")

    assert "退款金额" not in result.section_hints


def test_no_domain_means_no_hint() -> None:
    assert QueryRewriter().rewrite("今天天气怎么样").section_hints == ()
    assert detect_domain("今天天气怎么样") is None


def test_a_policy_name_frame_does_not_override_the_question_topic() -> None:
    """退款政策对延迟发货怎么规定 asks about delivery, not 适用范围."""
    result = QueryRewriter().rewrite("退款政策对延迟发货怎么规定？")

    assert result.section_hints == ()
    assert result.search_text == result.original


def test_sections_can_be_switched_off() -> None:
    result = QueryRewriter(sections=False).rewrite("严重延迟应该怎么处理")

    assert result.section_hints == ()
    assert "处理方式" not in result.search_text


# ------------------------------------------------------------ the wrapper
def test_wrapper_hands_the_search_text_to_the_inner_retriever() -> None:
    inner = _RecordingSearch()
    search = RewritingPolicySearch(inner, QueryRewriter())

    hits = search.search(ALICE, "三十天内退款", limit=3)

    assert hits == [_chunk()]
    assert inner.queries == [search.last_rewrite.search_text]  # type: ignore[union-attr]
    assert inner.queries[0].startswith("三十天内退款")
    assert "30天" in inner.queries[0]


def test_wrapper_does_not_call_the_inner_retriever_for_an_empty_query() -> None:
    inner = _RecordingSearch()
    search = RewritingPolicySearch(inner, QueryRewriter())

    assert search.search(ALICE, "  ") == []
    assert inner.queries == []


def test_wrapper_forwards_the_dense_decision() -> None:
    inner = _RecordingSearch(dense_decision="low_coverage")

    assert RewritingPolicySearch(inner, QueryRewriter()).last_dense_decision == "low_coverage"


def test_wrapper_does_not_invent_a_dense_decision() -> None:
    """A sparse-only inner has no decision; the attribute must stay missing."""
    search = RewritingPolicySearch(_PlainSearch(), QueryRewriter())

    assert getattr(search, "last_dense_decision", None) is None
    with pytest.raises(AttributeError):
        search.last_dense_decision  # noqa: B018


def test_wrapper_rewrites_the_dense_gate_input_too(factory) -> None:  # noqa: ANN001
    with factory() as session:
        hybrid = HybridPolicySearch(
            session,
            sparse=PolicySearchService(session, max_results=3, min_score=0.01),
            embedder=None,
            max_results=3,
        )
        search = RewritingPolicySearch(hybrid, QueryRewriter())
        search.search(ALICE, "退款一百二十九元", limit=3)

        # The wrapper's own reading of `last_dense_decision` comes from the
        # inner hybrid, which saw the rewritten text.
        assert search.last_dense_decision == "no_embedder"
        assert search.last_rewrite is not None
        assert "129元" in search.last_rewrite.search_text


# ------------------------------------------------------------- assembly
def test_build_rewriter_follows_the_master_switch() -> None:
    assert build_rewriter(Settings(environment="test")) is not None
    assert build_rewriter(
        Settings(environment="test", retrieval_query_rewrite_enabled=False)
    ) is None


def test_build_rewriter_passes_the_configured_limits() -> None:
    rewriter = build_rewriter(
        Settings(
            environment="test",
            retrieval_query_rewrite_numbers=False,
            retrieval_query_rewrite_synonyms=False,
            retrieval_query_rewrite_sections=False,
            retrieval_query_rewrite_max_section_hints=1,
            retrieval_query_rewrite_max_expansions=3,
        )
    )

    assert rewriter is not None
    assert (rewriter.numbers, rewriter.synonyms, rewriter.sections) == (False, False, False)
    assert rewriter.max_section_hints == 1
    assert rewriter.max_expansions == 3

    capped = build_rewriter(
        Settings(
            environment="test",
            retrieval_query_rewrite_max_section_hints=9,
            retrieval_query_rewrite_max_expansions=99,
        )
    )

    assert capped is not None
    assert capped.max_section_hints == 2
    assert capped.max_expansions == 8


def test_build_policy_search_wraps_the_sparse_channel(factory) -> None:  # noqa: ANN001
    with factory() as session:
        search = build_policy_search(session, Settings(environment="test"))

    assert isinstance(search, RewritingPolicySearch)
    assert isinstance(search.inner, PolicySearchService)


def test_build_policy_search_wraps_the_hybrid_channel(factory) -> None:  # noqa: ANN001
    settings = Settings(
        environment="test",
        retrieval_dense_enabled=True,
        embedding_base_url="http://127.0.0.1:1234/v1",
        embedding_model="text-embedding-bge-m3",
    )
    with factory() as session:
        search = build_policy_search(session, settings)

    assert isinstance(search, RewritingPolicySearch)
    assert isinstance(search.inner, HybridPolicySearch)


def test_switching_the_rewrite_off_returns_the_old_object(factory) -> None:  # noqa: ANN001
    settings = Settings(environment="test", retrieval_query_rewrite_enabled=False)
    with factory() as session:
        search = build_policy_search(session, settings)

    assert type(search) is PolicySearchService


def test_rewrite_defaults_are_the_documented_defaults() -> None:
    settings = Settings(environment="test")

    assert settings.retrieval_query_rewrite_enabled is True
    assert settings.retrieval_query_rewrite_numbers is True
    assert settings.retrieval_query_rewrite_synonyms is True
    assert settings.retrieval_query_rewrite_sections is True
    assert settings.retrieval_query_rewrite_max_section_hints == 1
    assert settings.retrieval_query_rewrite_max_expansions == 8
