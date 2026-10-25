"""The gate every statement passes before it touches the database.

Three rules, in this order:

1. **Shape** — one ``SELECT``, one whitelisted table, whitelisted columns,
   whitelisted functions, a ``LIMIT`` inside the ceiling.
2. **Scope** — the tenant and the owner are injected by the executor, never
   by the statement. A statement that mentions them is refused, so there is
   no way to write a predicate that would widen the scope.
3. **Binding** — every value is a parameter. The only text that reaches the
   SQL string is an inline literal from a fixed list (the date formats).

The guard is the reason this feature is safe to expose: the planner produces
a plan, the builder produces a tree, and the tree still has to pass here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.sql.errors import SqlRejected
from app.sql.parser import (
    BetweenOp,
    ColumnRef,
    Comparison,
    Expr,
    FuncCall,
    InlineLiteral,
    InList,
    IsNull,
    LikeOp,
    Literal,
    Logical,
    NotExpr,
    Param,
    SelectStatement,
    Star,
    parse_select,
    walk_columns,
    walk_functions,
    walk_params,
)
from app.sql.schema import DATASETS, Dataset

__all__ = ["SqlGuard", "DEFAULT_FUNCTIONS", "DEFAULT_INLINE_LITERALS"]


# The functions a statement may call. Nothing here can write, and nothing
# here can reach the file system or the network.
DEFAULT_FUNCTIONS: frozenset[str] = frozenset(
    {
        "COUNT",
        "SUM",
        "COALESCE",
        "MIN",
        "MAX",
        "AVG",
        "STRFTIME",
        "TO_CHAR",
        "LOWER",
        "UPPER",
    }
)

# The only text allowed inside the SQL string. Everything else is bound.
DEFAULT_INLINE_LITERALS: frozenset[str] = frozenset(
    {"%Y-%m", "%Y-%m-%d", "YYYY-MM", "YYYY-MM-DD"}
)


class SqlGuard:
    """Checks a statement against the whitelist and scopes it."""

    def __init__(
        self,
        datasets: Mapping[str, Dataset] | None = None,
        *,
        max_limit: int = 200,
        allowed_functions: Iterable[str] = DEFAULT_FUNCTIONS,
        inline_literals: Iterable[str] = DEFAULT_INLINE_LITERALS,
        max_literal_length: int = 200,
    ) -> None:
        self.datasets = dict(datasets or DATASETS)
        self.max_limit = max_limit
        self.allowed_functions = frozenset(name.upper() for name in allowed_functions)
        self.inline_literals = frozenset(inline_literals)
        self.max_literal_length = max_literal_length

    # ------------------------------------------------------------- parsing
    def parse(self, sql: str) -> SelectStatement:
        return parse_select(sql)

    def validate(self, sql: str) -> SelectStatement:
        """Parse and check a statement that came from outside the builder."""
        statement = self.parse(sql)
        self.check(statement)
        return statement

    # -------------------------------------------------------------- checks
    def dataset_for(self, statement: SelectStatement) -> Dataset:
        dataset = self.datasets.get(statement.table.lower())
        if dataset is None:
            raise SqlRejected("UNKNOWN_TABLE", f"table {statement.table!r} is not readable")
        return dataset

    def check(
        self,
        statement: SelectStatement,
        *,
        allowed_params: Iterable[str] = (),
    ) -> None:
        """Check the statement against the whitelist."""
        dataset = self.dataset_for(statement)
        permitted = set(allowed_params)

        if statement.limit is None:
            raise SqlRejected("LIMIT_MISSING", "a LIMIT is required")
        if statement.limit > self.max_limit:
            raise SqlRejected(
                "LIMIT_TOO_LARGE", f"LIMIT {statement.limit} exceeds {self.max_limit}"
            )
        if statement.limit < 1:
            raise SqlRejected("LIMIT_TOO_LARGE", "LIMIT must be positive")

        aliases = {item.alias for item in statement.items if item.alias}
        for item in statement.items:
            self._check_expr(item.expr, dataset, permitted, allow_star=False)
        for expr in statement.group_by:
            self._check_group_expr(expr, dataset, aliases)
        for column, _ in statement.order_by:
            self._check_column(column, dataset, aliases)
        if statement.where is not None:
            self._check_expr(statement.where, dataset, permitted, allow_star=False)

    def _check_group_expr(
        self,
        expr: Expr,
        dataset: Dataset,
        aliases: set[str],
    ) -> None:
        """A grouping key may be a column or the expression behind an alias."""
        for column in walk_columns(expr):
            self._check_column(column, dataset, aliases)

    def _check_column(
        self,
        column: ColumnRef,
        dataset: Dataset,
        aliases: set[str],
    ) -> None:
        name = column.name.lower()
        if name in aliases:
            return
        if name not in dataset.columns:
            raise SqlRejected(
                "UNKNOWN_COLUMN", f"column {column.name!r} is not readable from {dataset.table}"
            )

    def _check_expr(
        self,
        expr: Expr,
        dataset: Dataset,
        permitted: set[str],
        *,
        allow_star: bool,
    ) -> None:
        if isinstance(expr, Star):
            if allow_star:
                return
            raise SqlRejected("STAR_SELECT", "'*' may only appear inside COUNT(*)")
        if isinstance(expr, ColumnRef):
            self._check_column(expr, dataset, set())
            return
        if isinstance(expr, Literal):
            if len(expr.value) > self.max_literal_length:
                raise SqlRejected("LITERAL_TOO_LONG", "the literal is too long")
            return
        if isinstance(expr, InlineLiteral):
            if expr.value not in self.inline_literals:
                raise SqlRejected(
                    "INLINE_LITERAL_NOT_ALLOWED",
                    f"{expr.value!r} may not be written into the statement",
                )
            return
        if isinstance(expr, Param):
            if expr.name not in permitted:
                raise SqlRejected("PARAM_NOT_ALLOWED", f"parameter :{expr.name} is not allowed")
            return
        if isinstance(expr, FuncCall):
            name = expr.name.upper()
            if name not in self.allowed_functions:
                raise SqlRejected("UNKNOWN_FUNCTION", f"function {name} is not allowed")
            if expr.star and name != "COUNT":
                raise SqlRejected("STAR_SELECT", "'*' may only appear inside COUNT(*)")
            for arg in expr.args:
                self._check_expr(arg, dataset, permitted, allow_star=expr.star)
            return
        if isinstance(expr, Comparison):
            self._check_expr(expr.left, dataset, permitted, allow_star=False)
            self._check_expr(expr.right, dataset, permitted, allow_star=False)
            return
        if isinstance(expr, Logical):
            for item in expr.items:
                self._check_expr(item, dataset, permitted, allow_star=False)
            return
        if isinstance(expr, (NotExpr, InList, LikeOp, BetweenOp, IsNull)):
            for node in _children(expr):
                self._check_expr(node, dataset, permitted, allow_star=False)
            return

    # --------------------------------------------------------------- scope
    def scope(
        self,
        statement: SelectStatement,
        *,
        tenant_param: str = "tenant_id",
        owner_param: str = "user_id",
    ) -> SelectStatement:
        """Add the tenant and owner predicates the statement may not write."""
        dataset = self.dataset_for(statement)
        tenant_column, owner_column = dataset.scope_columns
        predicates: list[Expr] = [
            Comparison(ColumnRef(None, tenant_column), "=", Param(tenant_param)),
            Comparison(ColumnRef(None, owner_column), "=", Param(owner_param)),
        ]
        if statement.where is not None:
            predicates.append(statement.where)
        return SelectStatement(
            items=statement.items,
            table=statement.table,
            alias=statement.alias,
            where=Logical("and", tuple(predicates)),
            group_by=statement.group_by,
            order_by=statement.order_by,
            limit=statement.limit,
        )

    def require_scope(self, statement: SelectStatement) -> None:
        """Fail unless the tenant predicate is a top-level conjunct."""
        dataset = self.dataset_for(statement)
        tenant_column = dataset.scope_columns[0]
        where = statement.where
        if where is None:
            raise SqlRejected("SCOPE_MISSING", "the statement has no scope predicate")
        items = where.items if isinstance(where, Logical) and where.op == "and" else (where,)
        for item in items:
            if _is_scope_predicate(item, tenant_column):
                return
        raise SqlRejected(
            "SCOPE_NOT_TOP_LEVEL",
            f"{tenant_column} must be a top-level predicate of the statement",
        )

    def referenced_columns(self, statement: SelectStatement) -> set[str]:
        names: set[str] = set()
        for item in statement.items:
            names.update(column.name.lower() for column in walk_columns(item.expr))
        for expr in statement.group_by:
            names.update(column.name.lower() for column in walk_columns(expr))
        for column, _ in statement.order_by:
            names.add(column.name.lower())
        if statement.where is not None:
            names.update(column.name.lower() for column in walk_columns(statement.where))
        return names

    def referenced_functions(self, statement: SelectStatement) -> set[str]:
        names: set[str] = set()
        for item in statement.items:
            names.update(walk_functions(item.expr))
        if statement.where is not None:
            names.update(walk_functions(statement.where))
        return names

    def referenced_params(self, statement: SelectStatement) -> set[str]:
        names: set[str] = set()
        for item in statement.items:
            names.update(walk_params(item.expr))
        if statement.where is not None:
            names.update(walk_params(statement.where))
        return names


def _is_scope_predicate(expr: Expr, column: str) -> bool:
    if not isinstance(expr, Comparison) or expr.op != "=":
        return False
    if isinstance(expr.left, ColumnRef) and expr.left.name.lower() == column:
        return True
    return isinstance(expr.right, ColumnRef) and expr.right.name.lower() == column


def _children(expr: Expr) -> tuple[Expr, ...]:
    if isinstance(expr, NotExpr):
        return (expr.item,)
    if isinstance(expr, InList):
        return (expr.operand, *expr.items)
    if isinstance(expr, LikeOp):
        return (expr.operand, expr.pattern)
    if isinstance(expr, BetweenOp):
        return (expr.operand, expr.low, expr.high)
    if isinstance(expr, IsNull):
        return (expr.operand,)
    return ()
