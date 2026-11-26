"""SQL text as tokens with their places, split into statements. One forward pass.

Comments, `{# #}` and `{% %}` tags are dropped; the SQL inside a `{% if %}` block stays.
A standalone `{{ ref('m') }}` or `{{ source('s', 't') }}` is one `dbt` token; any other
run-time value (`{{ x }}`, `${x}`, `$x`) becomes `{}` inside the word it sits in."""
from __future__ import annotations

import re
from typing import Dict, Iterator, List, NamedTuple, Optional, Tuple

from ..langs.sql_tables import literal_args, ref_version
from . import MAX_STATEMENT_TOKENS

WORD, QUOTED, NUMBER, STRING, OP, DBT = "word", "quoted", "number", "string", "op", "dbt"
_TWO_CHAR = frozenset({"::", "->", "=>", "||", "<=", ">=", "<>", "!=", "==", "**"})
_DBT_BODY = re.compile(r"-?\s*(ref|source)\s*\(([^()]*)\)\s*-?")
_SKIPS = {"/*": "*/", "{#": "#}", "{%": "%}"}


class Token(NamedTuple):
    kind: str
    text: str  # a word as written, a quoted name without its quotes, a dbt call's kind
    start: int
    end: int
    args: Optional[Tuple[str, ...]] = None  # a dbt call's string arguments, None when not literal
    version: Optional[str] = None  # ref('m', v=2)


class Statement(NamedTuple):
    tokens: List[Token]
    too_large: bool


def _ident_char(char: str) -> bool:
    return char.isalnum() or char == "_"


class _Finder:
    """`str.find` that remembers its last answer per needle; starts never decrease."""

    def __init__(self, text: str) -> None:
        self.text, self.cache = text, {}  # type: str, Dict[str, int]

    def find(self, needle: str, start: int) -> int:
        found = self.cache.get(needle)
        if found is not None and (found < 0 or found >= start):
            return found
        found = self.text.find(needle, start)
        self.cache[needle] = found
        return found


def _string_end(text: str, start: int) -> int:
    """Index after the `'` string at `start` (the same rule as sql_tables.mask)."""
    end, size = start + 1, len(text)
    while end < size and (text[end] != "'" or text[end:end + 2] == "''"):
        end += 2 if text[end] in "\\'" else 1
    return min(end + 1, size)


def _template_end(text: str, i: int, finder: _Finder) -> int:
    """End of a run-time value starting at `i` (`{{ }}`, `${ }`, `$name`), or -1."""
    if text.startswith("{{", i):
        close = finder.find("}}", i + 2)
        return close + 2 if close >= 0 else -1
    if text.startswith("${", i):
        close, line_end = finder.find("}", i + 2), finder.find("\n", i + 2)
        return close + 1 if close >= 0 and (line_end < 0 or close < line_end) else -1
    if text[i] == "$" and i + 1 < len(text) and (text[i + 1].isalpha() or text[i + 1] == "_"):
        end = i + 2
        while end < len(text) and _ident_char(text[end]):
            end += 1
        return end
    return -1


def tokens(text: str) -> Iterator[Token]:
    finder = _Finder(text)
    quoted_names = "`" not in text  # a file using backticks quotes names that way; "x" is then a string
    i, size = 0, len(text)
    while i < size:
        char, pair = text[i], text[i:i + 2]
        if char.isspace():
            i += 1
        elif pair == "--":
            end = finder.find("\n", i)
            i = size if end < 0 else end
        elif pair in _SKIPS:
            close = finder.find(_SKIPS[pair], i + 2)
            i = size if close < 0 else close + 2
        elif char == "'":
            end = _string_end(text, i)
            yield Token(STRING, "", i, end)
            i = end
        elif char in "`\"":
            line_end = finder.find("\n", i + 1)
            close = finder.find(char, i + 1)
            if close >= 0 and (line_end < 0 or close < line_end):
                kind = QUOTED if char == "`" or quoted_names else STRING
                yield Token(kind, text[i + 1:close] if kind == QUOTED else "", i, close + 1)
                i = close + 1
            else:
                yield Token(OP, char, i, i + 1)
                i += 1
        elif char.isdigit() or (char == "." and i + 1 < size and text[i + 1].isdigit()):
            end = i + 1
            while end < size and (text[end].isdigit() or text[end] == "."):
                end += 1
            if end < size and text[end] in "eE" and end + 1 < size and (text[end + 1].isdigit() or text[end + 1] in "+-"):
                end += 2
                while end < size and text[end].isdigit():
                    end += 1
            if end < size and _ident_char(text[end]):
                word, end = _word(text, end, finder, text[i:end])
                yield Token(WORD, word, i, end)
            else:
                yield Token(NUMBER, text[i:end], i, end)
            i = end
        elif _ident_char(char) or _template_end(text, i, finder) > 0:
            token = _dbt_call(text, i, finder)
            if token is None:
                word, end = _word(text, i, finder, "")
                token = Token(WORD, word, i, end)
            yield token
            i = token.end
        else:
            kind_text = pair if pair in _TWO_CHAR else char
            yield Token(OP, kind_text, i, i + len(kind_text))
            i += len(kind_text)


def _word(text: str, i: int, finder: _Finder, prefix: str) -> Tuple[str, int]:
    """(word, end) for name characters and run-time values glued together from `i`."""
    parts, size = [prefix], len(text)
    while i < size:
        if _ident_char(text[i]):
            start = i
            while i < size and _ident_char(text[i]):
                i += 1
            parts.append(text[start:i])
            continue
        end = _template_end(text, i, finder)
        if end < 0:
            if text[i] == "$":  # `$1`: a positional name, kept as written
                parts.append("$")
                i += 1
                continue
            break
        parts.append("{}")
        i = end
    return "".join(parts), i


def _dbt_call(text: str, i: int, finder: _Finder) -> Optional[Token]:
    """A `{{ ref() }}` or `{{ source() }}` that stands alone, or None."""
    if not text.startswith("{{", i):
        return None
    end = _template_end(text, i, finder)
    match = _DBT_BODY.fullmatch(text, i + 2, end - 2) if end > 0 else None
    if match is None or (end < len(text) and _ident_char(text[end])) or (i and _ident_char(text[i - 1])):
        return None
    args = literal_args(match.group(2))
    return Token(DBT, match.group(1), i, end, None if args is None else tuple(args), ref_version(match.group(2)))


def statements(text: str, limit: int = MAX_STATEMENT_TOKENS) -> Iterator[Statement]:
    """Token lists between `;`; a statement over `limit` tokens keeps only its first `limit`."""
    current: List[Token] = []
    too_large = False
    for token in tokens(text):
        if token.kind == OP and token.text == ";":
            if current or too_large:
                yield Statement(current, too_large)
            current, too_large = [], False
        elif len(current) < limit:
            current.append(token)
        else:
            too_large = True
    if current or too_large:
        yield Statement(current, too_large)
