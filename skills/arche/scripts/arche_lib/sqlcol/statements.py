"""Statement heads around a query: INSERT, CREATE … AS, CREATE TABLE (…), MERGE and
Hive's `FROM src INSERT … SELECT … INSERT … SELECT …`."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .ast import Assignment, Merge, Query, Select, Statement, TableName
from .parser import _DDL_SKIP, _PROCEDURAL, ParseError, Parser, _words
from .tokens import OP, QUOTED, WORD, Token

_CREATE_WORDS = _words("OR REPLACE TEMPORARY TEMP GLOBAL LOCAL VOLATILE TRANSIENT EXTERNAL MATERIALIZED UNLOGGED "
                       "SECURE MULTISET RECURSIVE")
_TEMP_WORDS = _words("TEMPORARY TEMP VOLATILE")


def statement(parser: Parser) -> List[Statement]:
    upper, start = parser.word(), parser.place()
    if parser.is_op("("):
        offset = 0
        while parser.is_op("(", offset):
            offset += 1
        if parser.word(offset) not in ("SELECT", "WITH", "VALUES"):
            return []  # an expression, as a macro body has, not a query
    if upper in ("SELECT", "VALUES") or parser.is_op("("):
        return [Statement("query", start, query=parser.query())]
    if upper == "WITH":
        ctes = parser.ctes()
        if parser.word() == "INSERT":
            found = _insert(parser)
            return [found._replace(query=found.query._replace(ctes=ctes + found.query.ctes))]
        body = parser.set_expr()
        return [Statement("query", start, query=Query(ctes, body, parser.order_tail(), start))]
    if upper == "INSERT":
        return [_insert(parser)]
    if upper == "CREATE":
        return _create(parser)
    if upper == "MERGE":
        return [_merge(parser)]
    if upper == "FROM":
        return _multi_insert(parser)
    if upper in _PROCEDURAL:
        raise ParseError("procedural", start)
    return []


def _insert_head(parser: Parser) -> Tuple[TableName, Optional[tuple], tuple, tuple]:
    """(target, column list, static partition values, dynamic partition columns)."""
    parser.expect_word("INSERT")
    parser.accept("OVERWRITE")
    parser.accept("INTO")
    if parser.word() in ("ALL", "FIRST"):
        raise ParseError("multi_table_insert", parser.place())
    parser.accept("TABLE")
    target = parser.table_name()
    constants: List[Assignment] = []
    partitions: List[Tuple[str, str]] = []
    if parser.word() == "PARTITION" and parser.is_op("(", 1):
        parser.i += 2
        while not parser.is_op(")"):
            name = parser.name()
            if parser.is_op("="):
                parser.advance()
                constants.append(Assignment(name[0], name[1], parser.expr()))
            else:
                partitions.append(name)
            if not parser.is_op(")"):
                parser.expect_op(",")
        parser.expect_op(")")
    if parser.word() == "IF" and parser.word(1) == "NOT":
        parser.i += 3
    columns = None
    if parser.is_op("(") and parser.word(1) not in ("SELECT", "WITH") and not parser.is_op("(", 1):
        columns = parser.name_list()
    if parser.word() == "BY" and parser.word(1) == "NAME":
        raise ParseError("by_name", parser.place())
    return target, columns, tuple(constants), tuple(partitions)


def _insert(parser: Parser) -> Statement:
    start = parser.place()
    target, columns, constants, partitions = _insert_head(parser)
    if parser.word() == "DEFAULT":
        raise ParseError("default_values", parser.place())
    return Statement("insert", start, target, columns, parser.query(), constants=constants, partitions=partitions)


def _ddl_columns(parser: Parser) -> List[Tuple[str, str]]:
    """Column names in a `(name type …, …)` definition list."""
    parser.expect_op("(")
    names: List[Tuple[str, str]] = []
    while parser.peek() is not None and not parser.is_op(")"):
        token = parser.peek()
        if token.kind in (WORD, QUOTED) and not (token.kind == WORD and token.text.upper() in _DDL_SKIP):
            names.append((token.text.lower(), token.text))
        level = 0
        while parser.peek() is not None:  # to the next top-level comma or the closing parenthesis
            token = parser.peek()
            if token.kind == OP and token.text in "(<":
                level += 1
            elif token.kind == OP and token.text in ")>":
                if level == 0 and token.text == ")":
                    break
                level -= 1
            elif token.kind == OP and token.text == "," and level == 0:
                parser.advance()
                break
            parser.advance()
    parser.expect_op(")")
    return names


def _create(parser: Parser) -> List[Statement]:
    start = parser.place()
    parser.expect_word("CREATE")
    temp = False
    while parser.word() in _CREATE_WORDS:
        temp = temp or parser.word() in _TEMP_WORDS
        parser.advance()
    if parser.word() not in ("TABLE", "VIEW"):
        return []
    view = parser.advance().text.upper() == "VIEW"
    if parser.word() == "IF" and parser.word(1) == "NOT":
        parser.i += 3
    target = parser.table_name()
    columns: Optional[List[Tuple[str, str]]] = None
    partitioned: List[Tuple[str, str]] = []  # Hive PARTITIONED BY: the last columns of the table
    if parser.is_op("(") and parser.word(1) not in ("SELECT", "WITH"):
        columns = _ddl_columns(parser)
    while parser.peek() is not None:
        upper = parser.word()
        if upper == "AS" and (parser.word(1) in ("SELECT", "WITH") or parser.is_op("(", 1)):
            parser.advance()
            break
        if upper in ("SELECT", "WITH"):
            break
        if upper in ("LIKE", "CLONE"):
            return []
        if upper == "PARTITIONED" and parser.word(1) == "BY" and parser.is_op("(", 2):
            parser.i += 2
            partitioned += _ddl_columns(parser)
        elif parser.is_op("("):
            parser.skip_parens()
        else:
            parser.advance()
    if parser.peek() is None:
        if (columns is None and not partitioned) or view:
            return []
        return [Statement("ddl", start, target, tuple((columns or []) + partitioned), temp=temp)]
    # In a CTAS the partition columns are the SELECT's last outputs; without a column list the outputs name them.
    listed = tuple(columns + partitioned) if columns is not None else None
    return [Statement("create", start, target, listed, parser.query(), temp=temp)]


def _merge(parser: Parser) -> Statement:
    start = parser.place()
    parser.expect_word("MERGE")
    parser.accept("INTO")
    target = parser.table_name()
    target_alias, _ = parser.source_alias()
    parser.expect_word("USING")
    sources: list = []
    parser.factor(sources, [], [])
    parser.expect_word("ON")
    on = parser.expr()
    conditions, updates, inserts = [], [], []
    while parser.accept("WHEN"):
        parser.accept("NOT")
        parser.expect_word("MATCHED")
        if parser.accept("BY"):
            parser.advance()
        if parser.accept("AND"):
            conditions.append(parser.expr())
        parser.expect_word("THEN")
        action = parser.advance().text.upper()
        if action == "UPDATE":
            parser.expect_word("SET")
            if parser.is_op("*"):
                raise ParseError("merge_star", parser.place())
            while True:
                parts, _ = parser.dotted()
                parser.expect_op("=")
                updates.append(Assignment(parts[-1].text.lower(), parts[-1].text, parser.expr()))
                if not parser.is_op(","):
                    break
                parser.advance()
        elif action == "INSERT":
            if parser.is_op("*") or parser.word() == "ROW":
                raise ParseError("merge_star", parser.place())
            names = parser.name_list() if parser.is_op("(") else None
            parser.expect_word("VALUES")
            parser.expect_op("(")
            values = []
            while not parser.is_op(")"):
                values.append(parser.expr())
                if not parser.is_op(")"):
                    parser.expect_op(",")
            parser.expect_op(")")
            if names is None or len(names) != len(values):
                raise ParseError("arity", start)
            inserts.extend(Assignment(name, written, value) for (name, written), value in zip(names, values))
        elif action != "DELETE":
            raise ParseError("merge_action", parser.place())
    merge = Merge(sources[0], target_alias, on, tuple(conditions), tuple(updates), tuple(inserts))
    return Statement("merge", start, target, merge=merge)


def _multi_insert(parser: Parser) -> List[Statement]:
    """Hive `FROM src INSERT OVERWRITE TABLE a SELECT … INSERT … SELECT …`: every SELECT reads `src`."""
    start = parser.place()
    parser.expect_word("FROM")
    sources: list = []
    using: list = []
    joins: list = []
    parser.from_clause(sources, using, joins)
    found: List[Statement] = []
    while parser.word() in ("INSERT", "SELECT"):
        head_start = parser.place()
        head = _insert_head(parser) if parser.word() == "INSERT" else None
        select = parser.select()
        merged = Select(select.items, tuple(sources) + select.sources, tuple(using) + select.using,
                        tuple(joins) + select.joins, select.where, select.groups, select.orders, select.start)
        query = Query((), merged, parser.order_tail(), head_start)
        if head is None:
            found.append(Statement("query", head_start, query=query))
        else:
            target, columns, constants, partitions = head
            found.append(Statement("insert", head_start, target, columns, query, constants=constants,
                                   partitions=partitions))
    if not found:
        raise ParseError("expected INSERT", parser.place())
    return found


def parse(tokens: Sequence[Token]) -> List[Statement]:
    """The statements in one `;`-separated token list: none when it is not a query or a write,
    several for a Hive `FROM … INSERT … INSERT …`. Raises ParseError."""
    parser = Parser(tokens)
    while parser.word() == "{}":
        parser.advance()  # {{ config(...) }} and other templates before the statement
    if parser.peek() is None:
        return []
    end = len(tokens)
    while end > parser.i and tokens[end - 1].kind == WORD and tokens[end - 1].text == "{}":
        end -= 1  # {{ macro(...) }} after the statement
    parser.tokens = tokens[:end]
    found = statement(parser)
    if found and parser.peek() is not None:
        found = [item._replace(tail=True) for item in found]
    return found
