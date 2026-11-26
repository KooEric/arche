"""A recursive descent parser for SQL query expressions: WITH, SELECT, set operations and FROM."""
from __future__ import annotations

from typing import FrozenSet, List, Optional, Tuple

from ..langs.sql_tables import table_name
from . import MAX_SELECT_ITEMS
from .ast import ORDER, Cte, Expr, FunctionSource, Item, Query, QuerySource, Select, SetOp, Star, TableName, TableSource, Values
from .expressions import _HARD_STOP, ExpressionParser, ParseError, _Collector, _words
from .tokens import DBT, NUMBER, QUOTED, STRING, WORD

__all__ = ["ParseError", "Parser"]

# Words after a table or a query in FROM that are never its alias (sql_tables._KEYWORDS and more).
_NOT_ALIAS = _words("""
    ANTI AS CLUSTER CROSS DELETE DIRECTORY DISTRIBUTE EXCEPT FETCH FOR FROM FULL GROUP HAVING INNER INSERT
    INTERSECT INTO JOIN LATERAL LEFT LIMIT LOCAL MERGE NATURAL OFFSET ON ORDER OUTER PARTITION PIVOT QUALIFY
    RIGHT SELECT SEMI SET SORT TABLE TABLESAMPLE UNION UNNEST UNPIVOT USING VALUES WHEN WHERE WINDOW WITH
    MINUS SAMPLE AT BEFORE APPLY ASOF RETURNING THEN ELSE END
""")
_JOIN_WORDS = _words("NATURAL INNER LEFT RIGHT FULL OUTER CROSS SEMI ANTI ASOF ANY")
_PROCEDURAL = _words("DECLARE BEGIN IF WHILE LOOP FOR CALL EXECUTE EXEC REPEAT CASE")
_DDL_SKIP = _words("PRIMARY CONSTRAINT FOREIGN UNIQUE CHECK INDEX KEY PERIOD LIKE EXCLUDE")


class Parser(ExpressionParser):
    # -- queries -----------------------------------------------------------------------
    def query(self) -> Query:
        start = self.place()
        self.enter()
        ctes = self.ctes()
        body = self.set_expr()
        orders = self.order_tail()
        self.leave()
        return Query(ctes, body, orders, start)

    def ctes(self) -> Tuple[Cte, ...]:
        ctes: List[Cte] = []
        if self.accept("WITH"):
            recursive = self.accept("RECURSIVE")
            while True:
                cte_start = self.place()
                name, _ = self.name()
                columns = tuple(n for n, _ in self.name_list()) if self.is_op("(") else ()
                self.expect_word("AS")
                self.accept("NOT")
                self.accept("MATERIALIZED")
                self.expect_op("(")
                ctes.append(Cte(name, columns, None if self.template_only() else self.query(), recursive, cte_start))
                self.expect_op(")")
                if not self.is_op(","):
                    break
                self.advance()
        return tuple(ctes)

    def template_only(self) -> bool:
        """True, past them, when only run-time values (`{{ macro() }}`) fill the parentheses."""
        offset = 0
        while self.word(offset) == "{}":
            offset += 1
        if offset and self.is_op(")", offset):
            self.i += offset
            return True
        return False

    def set_expr(self):
        start = self.place()
        branches = [self.term()]
        while self.word() in ("UNION", "INTERSECT", "EXCEPT", "MINUS"):
            self.advance()
            self.accept("ALL", "DISTINCT")
            if self.word() == "BY" and self.word(1) == "NAME":
                raise ParseError("by_name", self.place())
            branches.append(self.term())
        return branches[0] if len(branches) == 1 else SetOp(tuple(branches), start)

    def term(self):
        if self.is_op("("):
            self.advance()
            inner = self.query()
            self.expect_op(")")
            return inner
        if self.word() == "SELECT":
            return self.select()
        if self.word() == "VALUES":
            return self.values()
        raise ParseError("expected SELECT", self.place())

    def order_tail(self) -> Tuple[Expr, ...]:
        orders: List[Expr] = []
        while True:
            if self.word() == "ORDER" and self.word(1) == "BY":
                self.i += 2
                orders.extend(self.expr_list(_words("ASC DESC NULLS FIRST LAST"), ORDER))
            elif self.word() in ("LIMIT", "OFFSET"):
                self.advance()
                self.expr(frozenset({"OFFSET", "ROWS", "ROW"}))
                self.accept("ROWS", "ROW")
                if self.is_op(","):
                    self.advance()
                    self.expr()
            elif self.word() == "FETCH":
                while self.word() in ("FETCH", "FIRST", "NEXT", "ROW", "ROWS", "ONLY") or (
                        self.peek() is not None and self.peek().kind == NUMBER):
                    self.advance()
            else:
                return tuple(orders)

    def expr_list(self, noise: FrozenSet[str], context: int) -> List[Expr]:
        found = []
        while True:
            while self.word() in noise:
                self.advance()
            found.append(self.expr(noise, context))
            while self.word() in noise:
                self.advance()
            if not self.is_op(","):
                return found
            self.advance()

    def values(self) -> Values:
        start = self.place()
        self.expect_word("VALUES")
        rows = []
        while True:
            if self.is_op("("):
                self.advance()
                row = []
                while not self.is_op(")"):
                    row.append(self.expr())
                    if not self.is_op(")"):
                        self.expect_op(",")
                self.expect_op(")")
                rows.append(tuple(row))
            else:
                rows.append((self.expr(),))
            if not self.is_op(","):
                return Values(tuple(rows), start)
            self.advance()

    def select(self) -> Select:
        start = self.place()
        self.expect_word("SELECT")
        if self.accept("DISTINCT"):
            if self.word() == "ON" and self.is_op("(", 1):
                self.advance()
                self.skip_parens()
        self.accept("ALL")
        if self.accept("TOP"):  # TOP 10 or TOP (10)
            if self.is_op("("):
                self.skip_parens()
            else:
                self.advance()
            self.accept("PERCENT")
            if self.word() == "WITH" and self.word(1) == "TIES":
                self.i += 2
        if self.word() == "AS" and self.word(1) in ("STRUCT", "VALUE"):
            self.i += 2
        items = self.items()
        if self.word() == "INTO":
            raise ParseError("select_into", self.place())
        sources: List = []
        using: List[Tuple[str, ...]] = []
        joins: List[Expr] = []
        where: List[Expr] = []
        groups: List[Expr] = []
        orders: List[Expr] = []
        if self.accept("FROM"):
            self.from_clause(sources, using, joins)
        self.select_tail(where, groups, orders)
        return Select(items, tuple(sources), tuple(using), tuple(joins), tuple(where), tuple(groups), tuple(orders), start)

    def select_tail(self, where: List[Expr], groups: List[Expr], orders: List[Expr]) -> None:
        while True:
            upper = self.word()
            if upper in ("WHERE", "HAVING", "QUALIFY"):
                self.advance()
                where.append(self.expr())
            elif upper == "GROUP" and self.word(1) == "BY":
                self.i += 2
                self.accept("ALL", "DISTINCT")
                if self.word() == "GROUPING" and self.word(1) == "SETS":
                    self.i += 2  # the sets are read as one parenthesised expression
                if self.word() not in _HARD_STOP | {"WINDOW"}:
                    if not (self.peek() is None or self.is_op(")")):
                        groups.extend(self.expr_list(frozenset(), 0))
                if self.word() == "WITH" and self.word(1) in ("ROLLUP", "CUBE"):
                    self.i += 2
            elif upper in ("DISTRIBUTE", "SORT", "CLUSTER") and self.word(1) == "BY":
                self.i += 2
                orders.extend(self.expr_list(_words("ASC DESC NULLS FIRST LAST"), ORDER))
            elif upper == "WINDOW":
                self.advance()
                while True:
                    self.name()
                    self.expect_word("AS")
                    self.expect_op("(")
                    collector = _Collector()
                    self.over_body(collector)
                    self.expect_op(")")
                    start = self.tokens[self.i - 1].start
                    orders.append(Expr(start, start, tuple(collector.refs), tuple(collector.subqueries), False,
                                       collector.dynamic))
                    if not self.is_op(","):
                        break
                    self.advance()
            else:
                return

    def items(self) -> Tuple[Item, ...]:
        items: List[Item] = []
        while self.peek() is not None and not self.is_op(")") and self.word() not in _HARD_STOP:
            if len(items) >= MAX_SELECT_ITEMS:
                raise ParseError("too_many_items", self.place())
            start = self.place()
            star = self.star()
            if star is not None:
                items.append(Item(None, None, None, star, start, star.end))
            else:
                expr = self.expr()
                name = self.alias_after_item()
                if name is None and expr.bare:
                    name = (expr.refs[0].parts[-1], expr.refs[0].display)
                items.append(Item(name[0] if name else None, name[1] if name else None, expr, None, start,
                                  self.tokens[self.i - 1].end))
            if not self.is_op(","):
                break
            self.advance()  # a trailing comma before FROM is accepted
        return tuple(items)

    def alias_after_item(self) -> Optional[Tuple[str, str]]:
        if self.accept("AS"):
            token = self.peek()
            if token is not None and token.kind == STRING:
                self.advance()
                return None
            return self.name()
        token = self.peek()
        if token is not None and token.kind in (WORD, QUOTED) and (
                token.kind == QUOTED or self.word() not in _HARD_STOP | _words("WINDOW DISTRIBUTE SORT CLUSTER INSERT")):
            return self.name()
        return None

    def star(self) -> Optional[Star]:
        start = self.place()
        qualifier: Optional[Tuple[str, ...]] = None
        if self.is_op("*"):
            self.advance()
        else:
            offset = 0
            while (self.peek(offset) is not None and self.peek(offset).kind in (WORD, QUOTED)
                   and self.is_op(".", offset + 1)):
                if self.is_op("*", offset + 2):
                    qualifier = tuple(self.peek(k).text.lower() for k in range(0, offset + 1, 2))
                    self.i += offset + 3
                    break
                offset += 2
            else:
                return None
            if qualifier is None:
                return None
        except_: List[str] = []
        replace: List[Tuple[str, Expr]] = []
        rename: List[Tuple[str, str, str]] = []
        while True:
            upper = self.word()
            if upper in ("EXCEPT", "EXCLUDE") and self.is_op("(", 1) and self.word(2) not in ("SELECT", "WITH"):
                self.advance()
                except_.extend(name for name, _ in self.name_list())
            elif upper == "EXCLUDE":
                self.advance()
                except_.append(self.name()[0])
            elif upper == "REPLACE" and self.is_op("(", 1):
                self.i += 2
                while not self.is_op(")"):
                    expr = self.expr()
                    self.expect_word("AS")
                    replace.append((self.name()[0], expr))
                    if not self.is_op(")"):
                        self.expect_op(",")
                self.expect_op(")")
            elif upper == "RENAME":
                self.advance()
                grouped = self.is_op("(")
                if grouped:
                    self.advance()
                while True:
                    old = self.name()[0]
                    self.expect_word("AS")
                    new, written = self.name()
                    rename.append((old, new, written))
                    if not (grouped and self.is_op(",")):
                        break
                    self.advance()
                if grouped:
                    self.expect_op(")")
            else:
                break
        return Star(qualifier, start, self.tokens[self.i - 1].end, tuple(except_), tuple(replace), tuple(rename))

    # -- FROM ----------------------------------------------------------------------------
    def from_clause(self, sources: List, using: List[Tuple[str, ...]], joins: List[Expr]) -> None:
        self.factor(sources, using, joins)
        while self.peek() is not None:
            upper = self.word()
            if self.is_op(","):
                self.advance()
                self.factor(sources, using, joins)
            elif upper in _JOIN_WORDS or upper == "JOIN":
                while self.word() in _JOIN_WORDS:
                    self.advance()
                if self.accept("APPLY"):
                    self.factor(sources, using, joins)
                    continue
                self.expect_word("JOIN")
                self.factor(sources, using, joins)
                if self.accept("ON"):
                    joins.append(self.expr())
                elif self.accept("USING"):
                    using.append(tuple(name for name, _ in self.name_list()))
            elif upper == "LATERAL" and self.word(1) == "VIEW":
                self.i += 2
                self.accept("OUTER")
                start = self.place()
                inputs = self.expr()
                alias = self.name()[0] if self.word() != "AS" else None
                columns: List[str] = []
                if self.accept("AS"):
                    columns.append(self.name()[0])
                    while self.is_op(",") and self.peek(1) is not None and self.peek(1).kind in (WORD, QUOTED) \
                            and self.word(1) not in _NOT_ALIAS and not self.is_op(".", 2) and not self.is_op("(", 2):
                        self.advance()
                        columns.append(self.name()[0])
                sources.append(FunctionSource(inputs, alias, tuple(columns), start))
            else:
                return

    def factor(self, sources: List, using: List[Tuple[str, ...]], joins: List[Expr]) -> None:
        self.enter()
        start = self.place()
        self.accept("LATERAL")
        token = self.peek()
        if token is None:
            raise ParseError("expected table", start)
        if self.is_op("(") and self.word(1) == "{}":
            self.advance()
            if not self.template_only():
                raise ParseError("expected table", self.place())
            self.expect_op(")")
            alias, _ = self.source_alias()
            sources.append(TableSource(TableName(None, True, None, start), alias or "", ()))
        elif self.is_op("("):
            if self.word(1) in ("SELECT", "WITH", "VALUES") or self.is_op("(", 1) and self.word(2) in ("SELECT", "WITH"):
                self.advance()
                query = self.query()
                self.expect_op(")")
                alias, columns = self.source_alias()
                sources.append(QuerySource(query, alias, columns, start))
            else:
                self.advance()
                self.from_clause(sources, using, joins)
                self.expect_op(")")
                self.source_alias()
        elif self.word() == "VALUES":
            values = self.values()
            alias, columns = self.source_alias()
            sources.append(QuerySource(Query((), values, (), start), alias, columns, start))
        elif token.kind == WORD and self.is_op("(", 1) or self.word() == "TABLE" and self.is_op("(", 1):
            self.advance()
            inputs = self.expr_after_open()
            if self.word() == "WITH" and self.word(1) == "ORDINALITY":
                self.i += 2
            alias, columns = self.source_alias()
            sources.append(FunctionSource(inputs, alias, columns, start))
        else:
            table = self.table_name()
            self.table_suffixes()
            alias, columns = self.source_alias()
            default = table.name.rsplit(".", 1)[-1] if table.name else ""
            sources.append(TableSource(table, alias or default, columns))
        if self.word() in ("PIVOT", "UNPIVOT", "MATCH_RECOGNIZE"):
            raise ParseError("pivot", self.place())
        self.leave()

    def expr_after_open(self) -> Expr:
        """The arguments of a table function as one expression; the `(` is next."""
        start = self.place()
        collector = _Collector()
        self.paren(collector, 0)
        return Expr(start, self.tokens[self.i - 1].end, tuple(collector.refs), tuple(collector.subqueries), False,
                    collector.dynamic)

    def table_suffixes(self) -> None:
        while True:
            upper = self.word()
            if upper in ("TABLESAMPLE", "SAMPLE") and self.is_op("(", 1) or upper in ("AT", "BEFORE") and self.is_op("(", 1):
                self.advance()
                self.skip_parens()
            elif upper == "WITH" and self.is_op("(", 1):
                self.advance()
                self.skip_parens()
            elif upper == "FOR" and self.word(1) in ("SYSTEM_TIME", "SYSTEM_VERSION", "TIMESTAMP", "VERSION"):
                self.i += 2
                while self.word() in ("AS", "OF"):
                    self.advance()
                self.expr()
            else:
                return

    def source_alias(self) -> Tuple[Optional[str], Tuple[str, ...]]:
        alias = None
        if self.accept("AS"):
            alias = self.name()[0]
        elif self.peek() is not None and self.peek().kind in (WORD, QUOTED) and (
                self.peek().kind == QUOTED or self.word() not in _NOT_ALIAS | _JOIN_WORDS):
            alias = self.name()[0]
        columns: Tuple[str, ...] = ()
        if alias is not None and self.is_op("(") and self.peek(1) is not None and self.peek(1).kind in (WORD, QUOTED):
            columns = tuple(name for name, _ in self.name_list())
        return alias, columns

    def table_name(self) -> TableName:
        token = self.peek()
        if token is None:
            raise ParseError("expected table", self.place())
        start = token.start
        if token.kind == DBT:
            self.advance()
            return TableName(None, False, (token.text, token.args, token.version), start, token.end)
        if token.kind not in (WORD, QUOTED):
            raise ParseError("expected table", start)
        parts, _ = self.dotted()
        text = ".".join(f"`{part.text}`" if part.kind == QUOTED else part.text for part in parts)
        name = table_name(text)
        return TableName(name, name is not None and "{}" in name.rsplit(".", 1)[-1], None, start, parts[-1].end)
