"""只解析本项目愿意执行的那一种 SQL 形状。

本模块只做一件事：把文本变成一棵很小的树，再把这棵树渲染成 SQL 与参数。
「允不允许」是 ``app/sql/guard.py`` 的判断，不在这里——两边故意分开，解析器
才不必为了安全去猜语义，守卫面对的也永远是一棵已经成形、没有歧义的树。

文法小得刻意：

    SELECT <items> FROM <table> [WHERE <expr>]
           [GROUP BY <cols>] [ORDER BY <cols>] [LIMIT <n>]

此外的一切——第二条语句、join、子查询、集合运算、注释——在解析阶段就以
``SqlRejected`` 收场，不留到守卫去事后补救。

一条取舍：字面量与内联文本分开存（``Literal`` 与 ``InlineLiteral``）。前者
渲染成绑定参数，后者只能来自白名单常量；混为一谈的话，「每个值都当参数绑」
这条不变量会在渲染器里悄悄破掉。树上另有 ``walk_columns`` /
``walk_functions`` / ``walk_params`` 三个遍历器，守卫与审计靠它们看清一棵树
到底碰了哪些列、调了哪些函数、带出哪些参数。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from app.sql.errors import SqlRejected

__all__ = [
    "BetweenOp",
    "ColumnRef",
    "Comparison",
    "Expr",
    "FuncCall",
    "InList",
    "InlineLiteral",
    "IsNull",
    "LikeOp",
    "Literal",
    "Logical",
    "NotExpr",
    "Param",
    "SelectItem",
    "SelectStatement",
    "Star",
    "parse_select",
    "render",
    "walk_columns",
    "walk_functions",
    "walk_params",
]


@dataclass(frozen=True, slots=True)
class ColumnRef:
    table: str | None
    name: str


@dataclass(frozen=True, slots=True)
class Literal:
    """A value that will be bound as a parameter, never pasted into the SQL."""

    value: str


@dataclass(frozen=True, slots=True)
class InlineLiteral:
    """A value rendered into the SQL text; only the guard's whitelist allows it."""

    value: str


@dataclass(frozen=True, slots=True)
class Param:
    name: str


@dataclass(frozen=True, slots=True)
class Star:
    """``*`` — only ``COUNT(*)`` may contain one."""


@dataclass(frozen=True, slots=True)
class FuncCall:
    name: str
    args: tuple[Expr, ...] = ()
    star: bool = False


@dataclass(frozen=True, slots=True)
class Comparison:
    left: Expr
    op: str
    right: Expr


@dataclass(frozen=True, slots=True)
class Logical:
    op: str  # "and" | "or"
    items: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class NotExpr:
    item: Expr


@dataclass(frozen=True, slots=True)
class InList:
    operand: Expr
    items: tuple[Expr, ...]
    negated: bool = False


@dataclass(frozen=True, slots=True)
class LikeOp:
    operand: Expr
    pattern: Expr
    negated: bool = False


@dataclass(frozen=True, slots=True)
class BetweenOp:
    operand: Expr
    low: Expr
    high: Expr
    negated: bool = False


@dataclass(frozen=True, slots=True)
class IsNull:
    operand: Expr
    negated: bool = False


Expr = (
    ColumnRef
    | Literal
    | InlineLiteral
    | Param
    | Star
    | FuncCall
    | Comparison
    | Logical
    | NotExpr
    | InList
    | LikeOp
    | BetweenOp
    | IsNull
)


@dataclass(frozen=True, slots=True)
class SelectItem:
    expr: Expr
    alias: str | None = None


@dataclass(frozen=True, slots=True)
class SelectStatement:
    items: tuple[SelectItem, ...]
    table: str
    alias: str | None
    where: Expr | None
    group_by: tuple[Expr, ...]
    order_by: tuple[tuple[ColumnRef, str], ...]
    limit: int | None


# --------------------------------------------------------------- tokenizer
@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    text: str
    value: str


_RESERVED = frozenset(
    {
        "SELECT",
        "FROM",
        "WHERE",
        "GROUP",
        "BY",
        "ORDER",
        "LIMIT",
        "HAVING",
        "JOIN",
        "INNER",
        "LEFT",
        "RIGHT",
        "FULL",
        "CROSS",
        "ON",
        "UNION",
        "INTERSECT",
        "EXCEPT",
        "AND",
        "OR",
        "NOT",
        "IN",
        "LIKE",
        "BETWEEN",
        "IS",
        "NULL",
        "AS",
        "ASC",
        "DESC",
        "DISTINCT",
    }
)


def _tokenize(sql: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    size = len(sql)
    while index < size:
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        if sql.startswith("--", index):
            raise SqlRejected("COMMENT", "line comments are not allowed")
        if sql.startswith("/*", index):
            raise SqlRejected("COMMENT", "block comments are not allowed")
        if char == "'":
            value, index = _read_quoted(sql, index, "'")
            tokens.append(_Token("string", value, value))
            continue
        if char == '"':
            value, index = _read_quoted(sql, index, '"')
            tokens.append(_Token("ident", value, value))
            continue
        if char.isdigit():
            value, index = _read_number(sql, index)
            tokens.append(_Token("number", value, value))
            continue
        if char.isalpha() or char == "_":
            value, index = _read_ident(sql, index)
            tokens.append(_Token("ident", value, value))
            continue
        if char == ":":
            end = index + 1
            while end < size and (sql[end].isalnum() or sql[end] == "_"):
                end += 1
            if end == index + 1:
                raise SqlRejected("SYNTAX", "a parameter name must follow ':'")
            tokens.append(_Token("param", sql[index:end], sql[index + 1 : end]))
            index = end
            continue
        pair = sql[index : index + 2]
        if pair in {"!=", "<>", "<=", ">="}:
            tokens.append(_Token("punct", pair, pair))
            index += 2
            continue
        if char in {"(", ")", ",", "*", "=", "<", ">", ";"}:
            tokens.append(_Token("punct", char, char))
            index += 1
            continue
        raise SqlRejected("SYNTAX", f"unexpected character {char!r}")
    return tokens


def _read_quoted(sql: str, start: int, quote: str) -> tuple[str, int]:
    index = start + 1
    size = len(sql)
    chunk: list[str] = []
    while index < size:
        char = sql[index]
        if char == quote:
            if index + 1 < size and sql[index + 1] == quote:
                chunk.append(quote)
                index += 2
                continue
            return "".join(chunk), index + 1
        chunk.append(char)
        index += 1
    raise SqlRejected("SYNTAX", "unterminated string")


def _read_number(sql: str, start: int) -> tuple[str, int]:
    index = start
    size = len(sql)
    while index < size and sql[index].isdigit():
        index += 1
    if index < size and sql[index] == ".":
        index += 1
        while index < size and sql[index].isdigit():
            index += 1
    return sql[start:index], index


def _read_ident(sql: str, start: int) -> tuple[str, int]:
    index = start
    size = len(sql)
    while index < size and (sql[index].isalnum() or sql[index] == "_"):
        index += 1
    return sql[start:index], index


# ------------------------------------------------------------------ parser
class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    # -- token helpers
    def peek(self) -> _Token | None:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def peek_next(self) -> _Token | None:
        if self.pos + 1 < len(self.tokens):
            return self.tokens[self.pos + 1]
        return None

    def take(self) -> _Token:
        token = self.peek()
        if token is None:
            raise SqlRejected("SYNTAX", "the statement ended early")
        self.pos += 1
        return token

    def at_keyword(self, word: str) -> bool:
        token = self.peek()
        return token is not None and token.kind == "ident" and token.value.upper() == word

    def eat_keyword(self, word: str) -> bool:
        if self.at_keyword(word):
            self.pos += 1
            return True
        return False

    def expect_keyword(self, word: str) -> None:
        if not self.eat_keyword(word):
            token = self.peek()
            raise SqlRejected(
                "SYNTAX", f"{word} expected but got {token.text if token else 'end of statement'}"
            )

    def at_punct(self, punct: str) -> bool:
        token = self.peek()
        return token is not None and token.kind == "punct" and token.text == punct

    def eat_punct(self, punct: str) -> bool:
        if self.at_punct(punct):
            self.pos += 1
            return True
        return False

    def expect_punct(self, punct: str) -> None:
        if not self.eat_punct(punct):
            token = self.peek()
            found = token.text if token else "end of statement"
            raise SqlRejected("SYNTAX", f"{punct!r} expected but got {found}")

    def expect_ident(self) -> str:
        token = self.take()
        if token.kind != "ident":
            raise SqlRejected("SYNTAX", f"a name expected but got {token.text!r}")
        return token.value

    # -- grammar
    def parse_statement(self) -> SelectStatement:
        token = self.peek()
        if token is None:
            raise SqlRejected("SYNTAX", "the statement is empty")
        if token.kind != "ident" or token.value.upper() != "SELECT":
            raise SqlRejected("NOT_SELECT", f"the statement starts with {token.text!r}")
        self.take()

        items = self.parse_select_list()
        self.expect_keyword("FROM")
        if self.at_punct("("):
            raise SqlRejected("SUBQUERY", "a table function is not allowed")
        table = self.expect_ident()

        alias: str | None = None
        if self.eat_keyword("AS"):
            alias = self.expect_ident()
        else:
            following = self.peek()
            if (
                following is not None
                and following.kind == "ident"
                and following.value.upper() not in _RESERVED
            ):
                alias = self.take().value

        where: Expr | None = None
        if self.eat_keyword("WHERE"):
            where = self.parse_expr()

        group_by: tuple[Expr, ...] = ()
        if self.eat_keyword("GROUP"):
            self.expect_keyword("BY")
            group_by = (self.parse_expr(),)
            while self.eat_punct(","):
                group_by += (self.parse_expr(),)

        order_by: tuple[tuple[ColumnRef, str], ...] = ()
        if self.eat_keyword("ORDER"):
            self.expect_keyword("BY")
            order_by = (self.parse_order_item(),)
            while self.eat_punct(","):
                order_by += (self.parse_order_item(),)

        limit: int | None = None
        if self.eat_keyword("LIMIT"):
            number = self.take()
            if number.kind != "number":
                raise SqlRejected("SYNTAX", "LIMIT needs a number")
            limit = int(number.value)

        self.eat_punct(";")
        trailing = self.peek()
        if trailing is not None:
            word = trailing.value.upper() if trailing.kind == "ident" else ""
            if word in {"UNION", "INTERSECT", "EXCEPT"}:
                raise SqlRejected("SET_OPERATION", f"{word} is not allowed")
            if word == "JOIN" or word in {"INNER", "LEFT", "RIGHT", "FULL", "CROSS"}:
                raise SqlRejected("SYNTAX", "joins are not allowed")
            if word == "HAVING":
                raise SqlRejected("SYNTAX", "HAVING is not allowed")
            raise SqlRejected("MULTI_STATEMENT", f"unexpected {trailing.text!r}")

        return SelectStatement(
            items=items,
            table=table,
            alias=alias,
            where=where,
            group_by=group_by,
            order_by=order_by,
            limit=limit,
        )

    def parse_select_list(self) -> tuple[SelectItem, ...]:
        items = [self.parse_select_item()]
        while self.eat_punct(","):
            items.append(self.parse_select_item())
        return tuple(items)

    def parse_select_item(self) -> SelectItem:
        if self.at_punct("*"):
            self.take()
            return SelectItem(expr=Star())
        expr = self.parse_expr()
        alias: str | None = None
        if self.eat_keyword("AS"):
            alias = self.expect_ident()
        return SelectItem(expr=expr, alias=alias)

    def parse_order_item(self) -> tuple[ColumnRef, str]:
        column = self.parse_column_ref()
        direction = "ASC"
        if self.eat_keyword("DESC"):
            direction = "DESC"
        else:
            self.eat_keyword("ASC")
        return column, direction

    def parse_expr(self) -> Expr:
        return self.parse_or()

    def parse_or(self) -> Expr:
        items = [self.parse_and()]
        while self.eat_keyword("OR"):
            items.append(self.parse_and())
        if len(items) == 1:
            return items[0]
        return Logical("or", tuple(items))

    def parse_and(self) -> Expr:
        items = [self.parse_not()]
        while self.eat_keyword("AND"):
            items.append(self.parse_not())
        if len(items) == 1:
            return items[0]
        return Logical("and", tuple(items))

    def parse_not(self) -> Expr:
        if self.eat_keyword("NOT"):
            return NotExpr(self.parse_not())
        return self.parse_predicate()

    def parse_predicate(self) -> Expr:
        left = self.parse_operand()
        token = self.peek()
        if token is not None and token.kind == "punct" and token.text in {
            "=",
            "!=",
            "<>",
            "<",
            "<=",
            ">",
            ">=",
        }:
            op = self.take().text
            return Comparison(left, op, self.parse_operand())
        if self.at_keyword("IS"):
            self.take()
            negated = self.eat_keyword("NOT")
            self.expect_keyword("NULL")
            return IsNull(left, negated)
        if self.at_keyword("IN"):
            self.take()
            return InList(left, self.parse_paren_list(), False)
        if self.at_keyword("LIKE"):
            self.take()
            return LikeOp(left, self.parse_operand(), False)
        if self.at_keyword("BETWEEN"):
            self.take()
            return self.parse_between(left, False)
        if self.at_keyword("NOT"):
            self.take()
            if self.at_keyword("IN"):
                self.take()
                return InList(left, self.parse_paren_list(), True)
            if self.at_keyword("LIKE"):
                self.take()
                return LikeOp(left, self.parse_operand(), True)
            if self.at_keyword("BETWEEN"):
                self.take()
                return self.parse_between(left, True)
            raise SqlRejected("SYNTAX", "NOT must be followed by IN, LIKE or BETWEEN")
        # A bare value is an expression of its own: a column, a function
        # call or a literal. This is what a select item and a group key
        # look like.
        return left

    def parse_between(self, operand: Expr, negated: bool) -> Expr:
        low = self.parse_operand()
        self.expect_keyword("AND")
        high = self.parse_operand()
        return BetweenOp(operand, low, high, negated)

    def parse_paren_list(self) -> tuple[Expr, ...]:
        self.expect_punct("(")
        items = [self.parse_expr()]
        while self.eat_punct(","):
            items.append(self.parse_expr())
        self.expect_punct(")")
        return tuple(items)

    def parse_operand(self) -> Expr:
        token = self.peek()
        if token is None:
            raise SqlRejected("SYNTAX", "an expression was expected")
        if token.kind == "number":
            self.take()
            return Literal(token.value)
        if token.kind == "string":
            self.take()
            return Literal(token.value)
        if token.kind == "param":
            self.take()
            return Param(token.value)
        if token.kind == "ident":
            following = self.peek_next()
            if following is not None and following.kind == "punct" and following.text == "(":
                return self.parse_function()
            return self.parse_column_ref()
        if token.kind == "punct" and token.text == "(":
            self.take()
            inner = self.parse_expr()
            self.expect_punct(")")
            return inner
        raise SqlRejected("SYNTAX", f"unexpected {token.text!r}")

    def parse_function(self) -> FuncCall:
        name = self.take().value
        self.expect_punct("(")
        if self.at_punct("*"):
            self.take()
            self.expect_punct(")")
            return FuncCall(name, (), star=True)
        args = [self.parse_expr()]
        while self.eat_punct(","):
            args.append(self.parse_expr())
        self.expect_punct(")")
        return FuncCall(name, tuple(args))

    def parse_column_ref(self) -> ColumnRef:
        token = self.take()
        if token.kind != "ident":
            raise SqlRejected("SYNTAX", f"a column name expected but got {token.text!r}")
        if self.at_punct("."):
            self.take()
            second = self.take()
            if second.kind != "ident":
                raise SqlRejected("SYNTAX", "a column name expected")
            return ColumnRef(token.value, second.value)
        return ColumnRef(None, token.value)


def parse_select(sql: str) -> SelectStatement:
    """Parse a statement, or refuse it with a reason code."""
    return _Parser(_tokenize(sql)).parse_statement()


# ----------------------------------------------------------------- walkers
def walk_columns(expr: Expr | None) -> Iterator[ColumnRef]:
    if expr is None:
        return
    if isinstance(expr, ColumnRef):
        yield expr
        return
    if isinstance(expr, FuncCall):
        for arg in expr.args:
            yield from walk_columns(arg)
        return
    if isinstance(expr, Comparison):
        yield from walk_columns(expr.left)
        yield from walk_columns(expr.right)
        return
    if isinstance(expr, Logical):
        for item in expr.items:
            yield from walk_columns(item)
        return
    if isinstance(expr, NotExpr):
        yield from walk_columns(expr.item)
        return
    if isinstance(expr, InList):
        yield from walk_columns(expr.operand)
        for item in expr.items:
            yield from walk_columns(item)
        return
    if isinstance(expr, LikeOp):
        yield from walk_columns(expr.operand)
        yield from walk_columns(expr.pattern)
        return
    if isinstance(expr, BetweenOp):
        yield from walk_columns(expr.operand)
        yield from walk_columns(expr.low)
        yield from walk_columns(expr.high)
        return
    if isinstance(expr, IsNull):
        yield from walk_columns(expr.operand)


def walk_functions(expr: Expr | None) -> Iterator[str]:
    if expr is None:
        return
    if isinstance(expr, FuncCall):
        yield expr.name.upper()
        for arg in expr.args:
            yield from walk_functions(arg)
        return
    if isinstance(expr, Comparison):
        yield from walk_functions(expr.left)
        yield from walk_functions(expr.right)
        return
    if isinstance(expr, Logical):
        for item in expr.items:
            yield from walk_functions(item)
        return
    if isinstance(expr, NotExpr):
        yield from walk_functions(expr.item)
        return
    if isinstance(expr, InList):
        yield from walk_functions(expr.operand)
        for item in expr.items:
            yield from walk_functions(item)
        return
    if isinstance(expr, LikeOp):
        yield from walk_functions(expr.operand)
        yield from walk_functions(expr.pattern)
        return
    if isinstance(expr, BetweenOp):
        yield from walk_functions(expr.operand)
        yield from walk_functions(expr.low)
        yield from walk_functions(expr.high)
        return
    if isinstance(expr, IsNull):
        yield from walk_functions(expr.operand)


def walk_params(expr: Expr | None) -> Iterator[str]:
    if expr is None:
        return
    if isinstance(expr, Param):
        yield expr.name
        return
    if isinstance(expr, FuncCall):
        for arg in expr.args:
            yield from walk_params(arg)
        return
    if isinstance(expr, Comparison):
        yield from walk_params(expr.left)
        yield from walk_params(expr.right)
        return
    if isinstance(expr, Logical):
        for item in expr.items:
            yield from walk_params(item)
        return
    if isinstance(expr, NotExpr):
        yield from walk_params(expr.item)
        return
    if isinstance(expr, InList):
        yield from walk_params(expr.operand)
        for item in expr.items:
            yield from walk_params(item)
        return
    if isinstance(expr, LikeOp):
        yield from walk_params(expr.operand)
        yield from walk_params(expr.pattern)
        return
    if isinstance(expr, BetweenOp):
        yield from walk_params(expr.operand)
        yield from walk_params(expr.low)
        yield from walk_params(expr.high)
        return
    if isinstance(expr, IsNull):
        yield from walk_params(expr.operand)


# ---------------------------------------------------------------- renderer
class _Renderer:
    """Turns the tree back into SQL, binding every value as a parameter."""

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params: dict[str, Any] = dict(params)
        self.counter = 0

    def bind(self, value: Any) -> str:
        name = f"p_{self.counter}"
        self.counter += 1
        self.params[name] = value
        return f":{name}"

    def expr(self, node: Expr) -> str:
        if isinstance(node, ColumnRef):
            if node.table:
                return f"{node.table}.{node.name}"
            return node.name
        if isinstance(node, Literal):
            return self.bind(node.value)
        if isinstance(node, InlineLiteral):
            return f"'{node.value}'"
        if isinstance(node, Param):
            return f":{node.name}"
        if isinstance(node, Star):
            return "*"
        if isinstance(node, FuncCall):
            if node.star:
                return f"{node.name.upper()}(*)"
            args = ", ".join(self.expr(arg) for arg in node.args)
            return f"{node.name.upper()}({args})"
        if isinstance(node, Comparison):
            return f"{self.expr(node.left)} {node.op} {self.expr(node.right)}"
        if isinstance(node, Logical):
            joiner = " AND " if node.op == "and" else " OR "
            text = joiner.join(self.expr(item) for item in node.items)
            return f"({text})" if node.op == "or" else text
        if isinstance(node, NotExpr):
            return f"NOT {self.expr(node.item)}"
        if isinstance(node, InList):
            values = ", ".join(self.expr(item) for item in node.items)
            keyword = "NOT IN" if node.negated else "IN"
            return f"{self.expr(node.operand)} {keyword} ({values})"
        if isinstance(node, LikeOp):
            keyword = "NOT LIKE" if node.negated else "LIKE"
            return f"{self.expr(node.operand)} {keyword} {self.expr(node.pattern)}"
        if isinstance(node, BetweenOp):
            keyword = "NOT BETWEEN" if node.negated else "BETWEEN"
            return (
                f"{self.expr(node.operand)} {keyword} "
                f"{self.expr(node.low)} AND {self.expr(node.high)}"
            )
        if isinstance(node, IsNull):
            keyword = "IS NOT NULL" if node.negated else "IS NULL"
            return f"{self.expr(node.operand)} {keyword}"
        raise SqlRejected("SYNTAX", f"cannot render {type(node).__name__}")


def render(
    statement: SelectStatement, params: Mapping[str, Any] | None = None
) -> tuple[str, dict[str, Any]]:
    """Render the statement and the parameters it needs."""
    renderer = _Renderer(params or {})
    items = []
    for item in statement.items:
        text = renderer.expr(item.expr)
        if item.alias:
            text = f"{text} AS {item.alias}"
        items.append(text)
    parts = [f"SELECT {', '.join(items)}", f"FROM {statement.table}"]
    if statement.where is not None:
        parts.append(f"WHERE {renderer.expr(statement.where)}")
    if statement.group_by:
        columns = ", ".join(renderer.expr(column) for column in statement.group_by)
        parts.append(f"GROUP BY {columns}")
    if statement.order_by:
        columns = ", ".join(
            f"{renderer.expr(column)} {direction}" for column, direction in statement.order_by
        )
        parts.append(f"ORDER BY {columns}")
    if statement.limit is not None:
        parts.append(f"LIMIT {statement.limit}")
    return " ".join(parts), renderer.params
