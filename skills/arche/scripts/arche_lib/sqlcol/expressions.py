"""Expressions, read as a flat run of operands and operators: enough to find the columns an
expression uses and the context they sit in (an aggregate, a window, a condition, an order key).
Depth is checked here, before Python's own recursion limit is near."""
from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from . import MAX_DEPTH
from .ast import AGG, CONDITION, ORDER, WINDOW, Expr, Query, Ref
from .tokens import DBT, NUMBER, OP, QUOTED, STRING, WORD, Token


class ParseError(Exception):
    """The statement is not SQL this parser reads; `reason` says why."""

    def __init__(self, reason: str, start: int) -> None:
        super().__init__(reason)
        self.reason, self.start = reason, start


def _words(text: str) -> FrozenSet[str]:
    return frozenset(text.split())


# Words that end an expression wherever they appear.
_HARD_STOP = _words("FROM WHERE GROUP HAVING ORDER LIMIT UNION INTERSECT EXCEPT MINUS QUALIFY INTO JOIN ON USING "
                    "AS WHEN THEN ELSE END SELECT")
# Words that join operands; never a column.
_KEYWORD_OPS = _words("AND OR NOT IS IN LIKE ILIKE RLIKE REGEXP SIMILAR BETWEEN ESCAPE COLLATE EXISTS ANY ALL "
                      "SOME OVERLAPS DIV MOD XOR")
_LITERAL_WORDS = _words("NULL TRUE FALSE UNKNOWN CURRENT_DATE CURRENT_TIMESTAMP CURRENT_TIME LOCALTIME "
                        "LOCALTIMESTAMP CURRENT_USER SESSION_USER")
_TYPED_LITERALS = _words("DATE TIME TIMESTAMP DATETIME TIMESTAMPTZ TIMESTAMP_LTZ TIMESTAMP_NTZ TIMESTAMP_TZ "
                         "JSON NUMERIC DECIMAL BIGNUMERIC")
_DATE_PARTS = _words("YEAR YEARS QUARTER QUARTERS MONTH MONTHS WEEK WEEKS DAY DAYS DAYOFWEEK DAYOFYEAR DOW DOY "
                     "HOUR HOURS MINUTE MINUTES SECOND SECONDS MILLISECOND MILLISECONDS MICROSECOND MICROSECONDS "
                     "NANOSECOND EPOCH ISOWEEK ISOYEAR WEEKDAY YEARMONTH")
# A bare date part is a unit only as the first argument (DATEADD(day, 1, x)) or, in BigQuery, the last
# (DATE_TRUNC(x, MONTH)); anywhere else, and named like DATE or D, it is a column.
_UNIT_FIRST = _words("DATEADD DATEDIFF DATEPART DATENAME TIMESTAMPADD TIMESTAMPDIFF DATE_PART DATE_TRUNC TIME_SLICE")
_UNIT_LAST = _words("DATE_TRUNC DATETIME_TRUNC TIMESTAMP_TRUNC DATE_DIFF DATETIME_DIFF TIMESTAMP_DIFF LAST_DAY "
                    "TIME_SLICE DATE_ADD DATE_SUB")
_DATE_FUNCTIONS = _words("DATEADD DATEDIFF DATE_ADD DATE_SUB DATE_DIFF DATE_TRUNC DATETIME_TRUNC TIMESTAMP_TRUNC "
                         "TIMESTAMPADD TIMESTAMPDIFF TIMESTAMP_DIFF DATETIME_DIFF DATE_PART DATEPART DATENAME "
                         "LAST_DAY TIME_SLICE TRUNC EXTRACT")
_CAST_FUNCTIONS = _words("CAST TRY_CAST SAFE_CAST CONVERT")
_CONDITION_FUNCTIONS = _words("IF IFF IIF")
AGGREGATES = _words("""
    SUM COUNT AVG MIN MAX ANY_VALUE ARRAY_AGG STRING_AGG LISTAGG GROUP_CONCAT COLLECT_LIST COLLECT_SET
    APPROX_COUNT_DISTINCT APPROX_DISTINCT APPROX_PERCENTILE APPROX_QUANTILES COUNT_IF COUNTIF SUM_IF AVG_IF
    STDDEV STDDEV_POP STDDEV_SAMP VARIANCE VAR_POP VAR_SAMP MEDIAN MODE PERCENTILE PERCENTILE_CONT
    PERCENTILE_DISC PERCENTILE_APPROX CORR COVAR_POP COVAR_SAMP BOOL_AND BOOL_OR BIT_AND BIT_OR EVERY
    LOGICAL_AND LOGICAL_OR MAX_BY MIN_BY ARG_MAX ARG_MIN OBJECT_AGG ARRAY_UNIQUE_AGG HLL KURTOSIS SKEWNESS
    FIRST LAST
""")
_CALL_NOISE = _words("DISTINCT ALL BOTH LEADING TRAILING FROM FOR SEPARATOR IGNORE RESPECT NULLS ASC DESC "
                     "FIRST LAST LIMIT USING FORMAT")
_OVER_NOISE = _words("PARTITION BY ORDER ROWS RANGE GROUPS BETWEEN UNBOUNDED PRECEDING FOLLOWING CURRENT ROW "
                     "AND ASC DESC NULLS FIRST LAST EXCLUDE TIES OTHERS NO")
# Constructors (ARRAY[a, b], STRUCT<x INT>(b), MAP(k, v)): never a column when one of these follows.
_CONSTRUCTORS = _words("ARRAY STRUCT MAP")


class _Collector:
    """What one expression has read so far."""

    def __init__(self) -> None:
        self.refs: List[Ref] = []
        self.subqueries: List[Tuple[Query, int]] = []
        self.dynamic = False
        self.calls = False
        self.operands = 0


class _Lambdas:
    """Lambda parameters met in one run of operands, and the last operand read: (first token, end, first ref)."""

    def __init__(self) -> None:
        self.params: set = set()
        self.drop_from: Optional[int] = None
        self.last: Optional[Tuple[int, int, int]] = None


class ExpressionParser:
    """Token helpers and expressions; `parser.Parser` adds queries, which expressions can hold."""

    def __init__(self, tokens: Sequence[Token]) -> None:
        self.tokens, self.i, self.depth = tokens, 0, 0
        self.angles: Dict[int, int] = {}  # `<` index -> index past its `>` (len(tokens) when unclosed)

    # -- token helpers ---------------------------------------------------------------
    def peek(self, offset: int = 0) -> Optional[Token]:
        index = self.i + offset
        return self.tokens[index] if index < len(self.tokens) else None

    def word(self, offset: int = 0) -> str:
        """The upper-case text of an unquoted word at `offset`, else ""."""
        token = self.peek(offset)
        return token.text.upper() if token is not None and token.kind == WORD else ""

    def is_op(self, text: str, offset: int = 0) -> bool:
        token = self.peek(offset)
        return token is not None and token.kind == OP and token.text == text

    def place(self) -> int:
        token = self.peek()
        return token.start if token is not None else (self.tokens[-1].end if self.tokens else 0)

    def advance(self) -> Token:
        if self.i >= len(self.tokens):
            raise ParseError("unexpected end", self.place())
        token = self.tokens[self.i]
        self.i += 1
        return token

    def expect_op(self, text: str) -> None:
        if not self.is_op(text):
            raise ParseError(f"expected {text}", self.place())
        self.i += 1

    def expect_word(self, text: str) -> None:
        if self.word() != text:
            raise ParseError(f"expected {text}", self.place())
        self.i += 1

    def accept(self, *words: str) -> bool:
        if self.word() in words:
            self.i += 1
            return True
        return False

    def enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ParseError("too_deep", self.place())

    def leave(self) -> None:
        self.depth -= 1

    def skip_parens(self) -> None:
        """Skip a balanced `( … )` group at the current token."""
        self.expect_op("(")
        level = 1
        while level and self.i < len(self.tokens):
            token = self.advance()
            if token.kind == OP and token.text in "()":
                level += 1 if token.text == "(" else -1
        if level:
            raise ParseError("unclosed", self.place())

    def name(self) -> Tuple[str, str]:
        """(lowercase, as written) of one word or quoted name."""
        token = self.peek()
        if token is None or token.kind not in (WORD, QUOTED):
            raise ParseError("expected name", self.place())
        self.i += 1
        return token.text.lower(), token.text

    def name_list(self) -> Tuple[Tuple[str, str], ...]:
        """`(a, b, …)`."""
        self.expect_op("(")
        names = []
        while not self.is_op(")"):
            names.append(self.name())
            if not self.is_op(")"):
                self.expect_op(",")
        self.expect_op(")")
        return tuple(names)

    # -- expressions -------------------------------------------------------------------
    def expr(self, stop: FrozenSet[str] = frozenset(), context: int = 0) -> Expr:
        start_index, collector = self.i, _Collector()
        self.expr_into(collector, stop, context)
        if self.i == start_index:
            raise ParseError("expected expression", self.place())
        start, end = self.tokens[start_index].start, self.tokens[self.i - 1].end
        bare = (len(collector.refs) == 1 and collector.operands == 1 and not collector.subqueries
                and self.i - start_index == 2 * len(collector.refs[0].parts) - 1)
        return Expr(start, end, tuple(collector.refs), tuple(collector.subqueries), bare, collector.dynamic,
                    collector.calls)

    def expr_into(self, collector: _Collector, stop: FrozenSet[str], context: int) -> None:
        expect, first_ref, lambdas = True, len(collector.refs), _Lambdas()
        while self.i < len(self.tokens):
            token, before, mark = self.peek(), self.i, len(collector.refs)
            upper = token.text.upper() if token.kind == WORD else ""
            if token.kind == OP:
                if token.text in (",", ")", "]", ";") or (token.text == "(" and not expect):
                    break
                if token.text == "->":
                    self.arrow(collector, lambdas)
                    expect = True
                    continue
                expect = self.operator(token.text, collector, context, expect, first_ref)
                if token.text == "(":
                    lambdas.last = (before, self.i, mark)
                continue
            if upper in _HARD_STOP or upper in stop:
                break
            if upper == "AT" and self.word(1) == "TIME" and self.word(2) == "ZONE":
                self.i += 3  # x AT TIME ZONE 'UTC'
                expect = True
                continue
            if not expect and upper not in _KEYWORD_OPS:
                break  # an alias follows
            if upper in _KEYWORD_OPS:
                self.keyword_op(upper)
                expect = True
                continue
            self.operand(collector, context)
            collector.operands += 1
            expect = False
            if self.i == before:
                self.i += 1
            lambdas.last = (before, self.i, mark)
        if lambdas.params:
            start = lambdas.drop_from
            collector.refs[start:] = [ref for ref in collector.refs[start:] if ref.parts[0] not in lambdas.params]

    def arrow(self, collector: _Collector, lambdas: _Lambdas) -> None:
        """`->`: a JSON operator before a string or a number, or as `->>`; else a lambda when bare names come
        right before it. Only that operand is looked at, so a run of arrows stays linear."""
        at = self.i
        arrow = self.advance()
        following = self.peek()
        if following is not None and following.kind == OP and following.text == ">" and following.start == arrow.end:
            self.advance()  # ->> (the tokenizer reads it as -> and >)
            return
        if lambdas.last is None or (following is not None and following.kind in (STRING, NUMBER)):
            return
        start, end, mark = lambdas.last
        params = collector.refs[mark:]
        if end != at or not self.lambda_params(start, end, params):
            return
        lambdas.params.update(ref.parts[0] for ref in params)
        if lambdas.drop_from is None:
            lambdas.drop_from = mark

    def lambda_params(self, start: int, end: int, params: List[Ref]) -> bool:
        """Whether tokens [start, end) are one one-part name or a parenthesised group of them."""
        if not params or any(len(ref.parts) != 1 for ref in params):
            return False
        if end - start == 1:
            return len(params) == 1 and self.tokens[start].kind in (WORD, QUOTED)
        first, last = self.tokens[start], self.tokens[end - 1]
        return (first.kind == OP and first.text == "(" and last.kind == OP and last.text == ")"
                and end - start == 2 * len(params) + 1)

    def operator(self, text: str, collector: _Collector, context: int, expect: bool, first_ref: int) -> bool:
        """Read one operator token; returns whether an operand is expected next."""
        self.advance()
        if text == "(":
            self.i -= 1
            self.paren(collector, context)
            collector.operands += 1
            return False
        if text == "[":
            self.enter()
            if not self.is_op("]"):
                self.expr_into(collector, frozenset(), context)
            while self.is_op(","):
                self.advance()
                self.expr_into(collector, frozenset(), context)
            self.expect_op("]")
            self.leave()
            return False
        if text == "::":
            self.skip_type()
            return False
        if text == ":":  # a Snowflake path (col:a.b) or a bind parameter (:p): no column after it
            while self.peek() is not None and (self.peek().kind in (WORD, QUOTED, NUMBER) or self.is_op(".")):
                self.advance()
            return False
        if text == "*":
            return not expect  # COUNT(*) has no operand after it; a * b does
        if text == "=>":
            if len(collector.refs) > first_ref and collector.refs[-1].start == self.tokens[self.i - 2].start:
                collector.refs.pop()  # a named argument, not a column
        elif text == ".":
            return expect
        return True

    def keyword_op(self, upper: str) -> None:
        self.advance()
        if upper == "IS":
            self.accept("NOT")
            if self.accept("DISTINCT"):
                self.expect_word("FROM")
        elif upper == "SIMILAR":
            self.accept("TO")

    def operand(self, collector: _Collector, context: int) -> None:
        token = self.peek()
        upper = token.text.upper() if token.kind == WORD else ""
        if token.kind in (STRING, NUMBER) or upper in _LITERAL_WORDS:
            self.advance()
        elif token.kind == DBT:
            self.advance()
            collector.dynamic = True
        elif upper == "CASE":
            self.case(collector, context)
        elif upper in _TYPED_LITERALS and self.peek(1) is not None and self.peek(1).kind == STRING:
            self.i += 2
        elif upper == "INTERVAL":
            self.interval(collector, context)
        elif upper in _CONSTRUCTORS and (self.is_op("[", 1) or self.is_op("(", 1) or self.is_op("<", 1)):
            self.constructor(collector, context)
        else:
            self.name_or_call(collector, context)

    def interval(self, collector: _Collector, context: int) -> None:
        self.advance()
        if self.peek() is None:
            raise ParseError("unexpected end", self.place())
        if self.peek().kind in (STRING, NUMBER):
            self.advance()
        elif not self.is_op("("):
            self.enter()
            self.operand(collector, context)
            self.leave()
        while self.word() in _DATE_PARTS or self.word() == "TO":
            self.advance()

    def constructor(self, collector: _Collector, context: int) -> None:
        """ARRAY[…], ARRAY<T>[…], STRUCT<…>(…), MAP(…): the elements are read, the word is not a column."""
        if self.is_op("(", 1):
            self.name_or_call(collector, context)
            return
        if self.is_op("[", 1):
            self.advance()
            return
        end = self.angle_end(self.i + 1)
        following = self.tokens[end] if end < len(self.tokens) else None
        if following is None or following.kind != OP or following.text not in ("[", "("):
            self.name_or_call(collector, context)  # array < 5: a column compared, not a type
            return
        self.i = end
        if following.text == "(":
            self.paren(collector, context)

    def dotted(self) -> Tuple[List[Token], bool]:
        """Name parts joined by `.`; True when it ends in `.*`."""
        parts = [self.advance()]
        while self.is_op(".") and self.peek(1) is not None:
            following = self.peek(1)
            if following.kind == OP and following.text == "*":
                self.i += 2
                return parts, True
            if following.kind not in (WORD, QUOTED):
                break
            self.i += 1
            parts.append(self.advance())
        return parts, False

    def name_or_call(self, collector: _Collector, context: int) -> None:
        parts, star = self.dotted()
        if star:
            return
        if self.is_op("(") and parts[-1].kind == WORD:
            self.call(".".join(part.text for part in parts).upper(), collector, context)
            return
        if any("{}" in part.text for part in parts):
            collector.dynamic = True
            return
        names = tuple(part.text.lower() for part in parts)
        collector.refs.append(Ref(names, parts[-1].text, parts[0].start, context))

    def paren(self, collector: _Collector, context: int) -> None:
        self.advance()
        self.enter()
        if self.word() in ("SELECT", "WITH"):
            collector.subqueries.append((self.query(), context))
        else:
            while not self.is_op(")"):
                before = self.i
                self.expr_into(collector, frozenset(), context)
                if self.is_op(","):
                    self.advance()
                elif self.i == before:
                    raise ParseError("unexpected token", self.place())
        self.expect_op(")")
        self.leave()

    def case(self, collector: _Collector, context: int) -> None:
        self.advance()
        self.enter()
        if self.word() != "WHEN":
            self.expr_into(collector, frozenset(), context | CONDITION)
        while self.accept("WHEN"):
            self.expr_into(collector, frozenset(), context | CONDITION)
            self.expect_word("THEN")
            self.expr_into(collector, frozenset(), context)
        if self.accept("ELSE"):
            self.expr_into(collector, frozenset(), context)
        self.expect_word("END")
        self.leave()

    def call(self, name: str, collector: _Collector, context: int) -> None:
        short = name.rsplit(".", 1)[-1]
        collector.calls = True
        self.advance()  # (
        self.enter()
        inner = _Collector()
        base = AGG if short in AGGREGATES else 0
        if short in ("EXTRACT", "DATE_PART") and self.peek(1) is not None and self.word(1) == "FROM":
            self.i += 2
        self.call_args(short, inner, base)
        self.expect_op(")")
        bits = 0
        if self.word() == "FILTER" and self.is_op("(", 1):
            self.i += 2
            self.expect_word("WHERE")
            self.expr_into(inner, frozenset(), AGG | CONDITION)
            self.expect_op(")")
        if self.word() == "WITHIN" and self.word(1) == "GROUP":
            self.i += 2
            self.expect_op("(")
            self.accept("ORDER")
            self.accept("BY")
            self.over_body(inner)
            self.expect_op(")")
        while self.word() in ("IGNORE", "RESPECT") and self.word(1) == "NULLS":
            self.i += 2
        if self.word() == "OVER":
            self.advance()
            bits = WINDOW
            if self.is_op("("):
                self.advance()
                self.over_body(collector, context)
                self.expect_op(")")
            elif self.peek() is not None and self.peek().kind in (WORD, QUOTED):
                self.advance()  # a named window
        for ref in inner.refs:
            collector.refs.append(ref._replace(context=ref.context | context | bits))
        collector.subqueries.extend((query, bits_ | context | bits) for query, bits_ in inner.subqueries)
        collector.dynamic = collector.dynamic or inner.dynamic
        self.leave()

    def call_args(self, short: str, inner: _Collector, base: int) -> None:
        order, position = 0, 0
        units: List[Tuple[int, Ref]] = []  # (argument position, a lone ref named like a date part)
        while self.i < len(self.tokens) and not self.is_op(")"):
            before, upper = self.i, self.word()
            if self.is_op(","):
                self.advance()
                position += 1
            elif upper == "ORDER" and self.word(1) == "BY":
                self.i += 2
                order = ORDER
            elif upper in _CALL_NOISE:
                self.advance()
            elif upper == "AS":
                self.advance()
                if short in _CAST_FUNCTIONS:
                    self.skip_type()
                elif self.peek() is not None and self.peek().kind in (WORD, QUOTED):
                    self.advance()
            elif upper in ("SELECT", "WITH") and position == 0 and not inner.refs and not inner.subqueries:
                inner.subqueries.append((self.query(), base))  # ARRAY(SELECT …)
            else:
                first = len(inner.refs)
                start = self.i
                context = base | order | (CONDITION if short in _CONDITION_FUNCTIONS and position == 0 else 0)
                self.expr_into(inner, _CALL_NOISE | {"AS"}, context)
                if (self.i - start == 1 and len(inner.refs) == first + 1 and len(inner.refs[-1].parts) == 1
                        and inner.refs[-1].parts[0].upper() in _DATE_PARTS):
                    units.append((position, inner.refs[-1]))
            if self.i == before:
                raise ParseError("unexpected token", self.place())
        drop = {id(ref) for at, ref in units
                if (at == 0 and short in _UNIT_FIRST) or (at == position and at > 0 and short in _UNIT_LAST)}
        if drop:  # a date part such as DAY, not a column
            inner.refs[:] = [ref for ref in inner.refs if id(ref) not in drop]

    def over_body(self, collector: _Collector, context: int = 0) -> None:
        while self.i < len(self.tokens) and not self.is_op(")"):
            before = self.i
            if self.word() in _OVER_NOISE or self.is_op(","):
                self.advance()
            else:
                self.expr_into(collector, _OVER_NOISE, context | ORDER)
            if self.i == before:
                raise ParseError("unexpected token", self.place())

    def skip_type(self) -> None:
        """A type name after `::` or `AS`: words, `(…)` arguments, `<…>` and `[]`."""
        while self.peek() is not None and self.peek().kind in (WORD, QUOTED) and self.word() not in _HARD_STOP:
            self.advance()
            if self.is_op("("):
                self.skip_parens()
            if self.is_op("<"):
                self.skip_angles()
            while self.is_op("[") and self.is_op("]", 1):
                self.i += 2
            if self.word() not in ("PRECISION", "VARYING", "WITH", "WITHOUT", "ZONE", "TIME", "LOCAL"):
                break

    def skip_angles(self) -> None:
        """Skip a balanced `< … >` type argument group at the current token."""
        self.i = self.angle_end(self.i)

    def angle_end(self, index: int) -> int:
        """The index past the `>` closing the `<` at `index`, or len(tokens) when it never closes. Every `<`
        met is remembered and a remembered span is jumped over, so no token is scanned twice."""
        known = self.angles.get(index)
        if known is not None:
            return known
        size, opened, at = len(self.tokens), [], index
        while at < size:
            token = self.tokens[at]
            if token.kind == OP and token.text == "<" and at != index and at in self.angles:
                at = self.angles[at]
                continue
            if token.kind == OP and token.text == "<":
                opened.append(at)
            elif token.kind == OP and token.text == ">" and opened:
                self.angles[opened.pop()] = at + 1
                if not opened:
                    return at + 1
            at += 1
        for start in opened:
            self.angles[start] = size
        return size

    def query(self) -> Query:
        raise NotImplementedError
