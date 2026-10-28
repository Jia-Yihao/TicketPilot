"""The guard is the security boundary, so it gets its own file.

Each test names one way a statement could try to do something it must not
do, and asserts the stable reason code it is refused with. The codes are
part of the contract: the ledger stores them and the API returns them.
"""

from __future__ import annotations

import pytest

from app.sql.errors import REASON_CODES, SqlRejected
from app.sql.guard import SqlGuard
from app.sql.parser import (
    ColumnRef,
    Comparison,
    FuncCall,
    InlineLiteral,
    Param,
    SelectItem,
    SelectStatement,
    Star,
)


@pytest.fixture
def guard() -> SqlGuard:
    return SqlGuard(max_limit=200)


def _reject(guard: SqlGuard, sql: str, *, allowed_params: set[str] | None = None) -> str:
    with pytest.raises(SqlRejected) as caught:
        statement = guard.parse(sql)
        guard.check(statement, allowed_params=allowed_params or set())
    return caught.value.reason


def test_a_plain_select_is_accepted(guard: SqlGuard) -> None:
    statement = guard.parse("SELECT COUNT(*) AS metric FROM orders LIMIT 10")
    guard.check(statement)
    assert statement.table == "orders"


def test_only_a_select_may_run(guard: SqlGuard) -> None:
    assert _reject(guard, "DELETE FROM orders LIMIT 10") == "NOT_SELECT"


def test_a_second_statement_is_refused(guard: SqlGuard) -> None:
    reason = _reject(guard, "SELECT id FROM orders LIMIT 1; DROP TABLE orders")
    assert reason == "MULTI_STATEMENT"


def test_comments_are_refused(guard: SqlGuard) -> None:
    assert _reject(guard, "SELECT id FROM orders -- take everything LIMIT 1") == "COMMENT"
    assert _reject(guard, "SELECT id /* all of it */ FROM orders LIMIT 1") == "COMMENT"


def test_a_broken_statement_is_refused(guard: SqlGuard) -> None:
    assert _reject(guard, "SELECT FROM orders LIMIT 1") == "SYNTAX"


def test_an_unknown_table_is_refused(guard: SqlGuard) -> None:
    assert _reject(guard, "SELECT id FROM secrets LIMIT 1") == "UNKNOWN_TABLE"


def test_an_unknown_column_is_refused(guard: SqlGuard) -> None:
    assert _reject(guard, "SELECT password FROM orders LIMIT 1") == "UNKNOWN_COLUMN"


def test_the_scope_columns_are_not_readable(guard: SqlGuard) -> None:
    """A statement may not even mention the owner columns."""
    assert (
        _reject(guard, "SELECT tenant_id FROM orders LIMIT 1") == "UNKNOWN_COLUMN"
    )
    assert _reject(guard, "SELECT user_id FROM orders LIMIT 1") == "UNKNOWN_COLUMN"


def test_an_unknown_function_is_refused(guard: SqlGuard) -> None:
    assert (
        _reject(guard, "SELECT LOAD_EXTENSION('x') FROM orders LIMIT 1")
        == "UNKNOWN_FUNCTION"
    )


def test_a_star_outside_count_is_refused(guard: SqlGuard) -> None:
    assert _reject(guard, "SELECT * FROM orders LIMIT 1") == "STAR_SELECT"


def test_a_subquery_is_refused(guard: SqlGuard) -> None:
    assert (
        _reject(guard, "SELECT id FROM (SELECT id FROM orders) LIMIT 1") == "SUBQUERY"
    )


def test_a_set_operation_is_refused(guard: SqlGuard) -> None:
    reason = _reject(
        guard, "SELECT id FROM orders UNION SELECT id FROM refunds LIMIT 1"
    )
    assert reason == "SET_OPERATION"


def test_a_join_is_refused(guard: SqlGuard) -> None:
    reason = _reject(
        guard, "SELECT o.id FROM orders o JOIN refunds r ON o.id = r.order_id LIMIT 1"
    )
    assert reason == "SYNTAX"


def test_a_missing_limit_is_refused(guard: SqlGuard) -> None:
    assert _reject(guard, "SELECT id FROM orders") == "LIMIT_MISSING"


def test_a_limit_above_the_ceiling_is_refused(guard: SqlGuard) -> None:
    assert _reject(guard, "SELECT id FROM orders LIMIT 100000") == "LIMIT_TOO_LARGE"


def test_an_unlisted_parameter_is_refused(guard: SqlGuard) -> None:
    reason = _reject(
        guard,
        "SELECT id FROM orders WHERE id = :secret LIMIT 1",
        allowed_params={"tenant_id"},
    )
    assert reason == "PARAM_NOT_ALLOWED"


def test_a_long_literal_is_refused(guard: SqlGuard) -> None:
    reason = _reject(guard, f"SELECT id FROM orders WHERE id = '{'x' * 300}' LIMIT 1")
    assert reason == "LITERAL_TOO_LONG"


def test_an_unlisted_inline_literal_is_refused(guard: SqlGuard) -> None:
    """Only the fixed date formats may be written into the statement."""
    statement = SelectStatement(
        items=(SelectItem(expr=ColumnRef(None, "id")),),
        table="orders",
        alias=None,
        where=Comparison(ColumnRef(None, "id"), "=", InlineLiteral("'x'")),
        group_by=(),
        order_by=(),
        limit=10,
    )
    with pytest.raises(SqlRejected) as caught:
        guard.check(statement)
    assert caught.value.reason == "INLINE_LITERAL_NOT_ALLOWED"


def test_a_statement_without_scope_is_refused(guard: SqlGuard) -> None:
    statement = guard.parse("SELECT id FROM orders LIMIT 1")
    with pytest.raises(SqlRejected) as caught:
        guard.require_scope(statement)
    assert caught.value.reason == "SCOPE_MISSING"


def test_a_buried_scope_predicate_is_refused(guard: SqlGuard) -> None:
    """The scope must be a top-level conjunct, not hidden inside an OR."""
    statement = guard.parse(
        "SELECT id FROM orders WHERE status = :status OR tenant_id = :tenant_id LIMIT 1"
    )
    with pytest.raises(SqlRejected) as caught:
        guard.require_scope(statement)
    assert caught.value.reason == "SCOPE_NOT_TOP_LEVEL"


def test_scoping_adds_the_owner_predicates(guard: SqlGuard) -> None:
    statement = guard.parse("SELECT COUNT(*) AS metric FROM orders LIMIT 10")
    scoped = guard.scope(statement)
    guard.require_scope(scoped)
    assert guard.referenced_params(scoped) == {"tenant_id", "user_id"}


def test_the_scope_is_added_next_to_the_other_predicates(guard: SqlGuard) -> None:
    statement = guard.parse(
        "SELECT id FROM orders WHERE status = :status LIMIT 1",
    )
    scoped = guard.scope(statement)
    assert guard.referenced_params(scoped) == {"status", "tenant_id", "user_id"}


def test_a_grouped_statement_names_its_bucket(guard: SqlGuard) -> None:
    statement = guard.parse(
        "SELECT STRFTIME('%Y-%m', created_at) AS bucket, COUNT(*) AS metric "
        "FROM refunds GROUP BY STRFTIME('%Y-%m', created_at) LIMIT 50"
    )
    guard.check(statement)
    assert "created_at" in guard.referenced_columns(statement)


def test_a_group_key_may_not_reach_another_column(guard: SqlGuard) -> None:
    reason = _reject(
        guard,
        "SELECT status AS bucket, COUNT(*) AS metric FROM refunds "
        "GROUP BY tenant_id LIMIT 50",
    )
    assert reason == "UNKNOWN_COLUMN"


def test_every_reason_code_is_stable() -> None:
    """The codes the tests assert on are part of the contract."""
    for code in (
        "NOT_SELECT",
        "MULTI_STATEMENT",
        "COMMENT",
        "SYNTAX",
        "UNKNOWN_TABLE",
        "UNKNOWN_COLUMN",
        "UNKNOWN_FUNCTION",
        "STAR_SELECT",
        "SUBQUERY",
        "SET_OPERATION",
        "LIMIT_MISSING",
        "LIMIT_TOO_LARGE",
        "PARAM_NOT_ALLOWED",
        "INLINE_LITERAL_NOT_ALLOWED",
        "LITERAL_TOO_LONG",
        "SCOPE_MISSING",
        "SCOPE_NOT_TOP_LEVEL",
        "NO_PLAN",
        "UNSUPPORTED_DIMENSION",
    ):
        assert code in REASON_CODES
    # Appending a code is allowed; losing one (or silently renaming) is not.
    assert len(REASON_CODES) == 19


def test_a_function_may_not_hide_a_star(guard: SqlGuard) -> None:
    statement = SelectStatement(
        items=(SelectItem(expr=FuncCall("SUM", (Star(),), star=True)),),
        table="orders",
        alias=None,
        where=None,
        group_by=(),
        order_by=(),
        limit=10,
    )
    with pytest.raises(SqlRejected) as caught:
        guard.check(statement)
    assert caught.value.reason == "STAR_SELECT"


def test_a_parameter_that_is_not_bound_is_refused(guard: SqlGuard) -> None:
    """The executor binds the scope; a statement cannot invent a value."""
    statement = SelectStatement(
        items=(SelectItem(expr=ColumnRef(None, "id")),),
        table="orders",
        alias=None,
        where=Comparison(ColumnRef(None, "id"), "=", Param("made_up")),
        group_by=(),
        order_by=(),
        limit=10,
    )
    with pytest.raises(SqlRejected) as caught:
        guard.check(statement, allowed_params={"tenant_id", "user_id"})
    assert caught.value.reason == "PARAM_NOT_ALLOWED"
