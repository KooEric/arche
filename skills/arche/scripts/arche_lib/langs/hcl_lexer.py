"""Tokens of HCL (Terraform) text: identifiers, strings, heredocs and punctuation.

Comments (`#`, `//`, `/* */`) are dropped. A quoted string keeps its literal text;
a `${ ... }` or `%{ ... }` template inside it is skipped with its nesting and marks
the string as templated. A heredoc (`<<EOF`, `<<-EOF`) is one string token.
"""
from __future__ import annotations

from typing import List, NamedTuple, Tuple

_IDENT_START = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_IDENT = _IDENT_START | frozenset("0123456789-")


class Token(NamedTuple):
    kind: str        # "ident", "string" or "punct"
    value: str
    line: int
    templated: bool = False


def tokens(text: str) -> Tuple[List[Token], bool]:
    """(tokens, broken). `broken` is True when a string, heredoc or comment does not end."""
    out: List[Token] = []
    i, line, n = 0, 1, len(text)
    while i < n:
        char = text[i]
        if char == "\n":
            line += 1
            i += 1
        elif char in " \t\r":
            i += 1
        elif char == "#" or text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                return out, True
            line += text.count("\n", i, end)
            i = end + 2
        elif char == '"':
            value, templated, end = _string(text, i + 1)
            if end < 0:
                return out, True
            out.append(Token("string", value, line, templated))
            line += text.count("\n", i, end)
            i = end
        elif text.startswith("<<", i) and _heredoc_marker(text, i) is not None:
            marker, body_start = _heredoc_marker(text, i)
            end = _heredoc_end(text, body_start, marker)
            if end < 0:
                return out, True
            out.append(Token("string", "", line, True))
            line += text.count("\n", i, end)
            i = end
        elif char in _IDENT_START:
            start = i
            while i < n and text[i] in _IDENT:
                i += 1
            out.append(Token("ident", text[start:i], line))
        else:
            out.append(Token("punct", char, line))
            i += 1
    return out, False


def _string(text: str, i: int) -> Tuple[str, bool, int]:
    """(literal text, templated, index after the closing quote); index -1 when it does not end."""
    parts: List[str] = []
    templated = False
    n = len(text)
    while i < n:
        char = text[i]
        if char == '"':
            return "".join(parts), templated, i + 1
        if char == "\n":
            return "", templated, -1
        if char == "\\" and i + 1 < n:
            parts.append(text[i + 1])
            i += 2
            continue
        if char in "$%" and text.startswith(char + "{", i + 1):
            parts.append(char + "{")  # `$${` and `%%{` are literal
            i += 3
            continue
        if char in "$%" and text.startswith("{", i + 1):
            end = _template_end(text, i + 2)
            if end < 0:
                return "", templated, -1
            templated = True
            parts.append("${}")
            i = end
            continue
        parts.append(char)
        i += 1
    return "", templated, -1


def _template_end(text: str, i: int) -> int:
    """Index after the `}` that closes a template opened just before i, or -1."""
    depth = 1
    n = len(text)
    while i < n:
        char = text[i]
        if char == '"':
            _, _, end = _string(text, i + 1)
            if end < 0:
                return -1
            i = end
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _heredoc_marker(text: str, i: int):
    """(marker, index of the body's first character) for `<<EOF` or `<<-EOF` at i, else None."""
    j = i + 2
    if j < len(text) and text[j] == "-":
        j += 1
    start = j
    while j < len(text) and text[j] in _IDENT:
        j += 1
    marker = text[start:j]
    rest_end = text.find("\n", j)
    if not marker or marker[0] not in _IDENT_START or rest_end < 0 or text[j:rest_end].strip():
        return None
    return marker, rest_end + 1


def _heredoc_end(text: str, i: int, marker: str) -> int:
    """Index just after the line holding only `marker`, or -1."""
    while i <= len(text):
        end = text.find("\n", i)
        row = text[i:] if end < 0 else text[i:end]
        if row.strip() == marker:
            return len(text) if end < 0 else end
        if end < 0:
            return -1
        i = end + 1
    return -1
