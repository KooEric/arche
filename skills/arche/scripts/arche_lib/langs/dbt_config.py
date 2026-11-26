"""The `alias` and `schema` a dbt model sets in `{{ config(...) }}`. Not a Jinja parser.

Jinja renders before SQL, so a config call counts even inside a `--` comment;
`{# #}` hides it. Later calls override earlier ones key by key, as in dbt. A
value that is not a single string literal is recorded as dynamic, and a call
inside `{% if %}` or `{% for %}` marks the whole result `branched`.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

KEYS = ("alias", "schema")
MAX_CALL_CHARS = 10_000
_CALL = re.compile(r"\{\{-?\s*config\s*\(")
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_BLOCK_TAG = re.compile(r"\{%-?\s*(if|for|endif|endfor)\b")
_ARGUMENT = re.compile(r"\s*([A-Za-z_]\w*)\s*=(.*)", re.S)
_LITERAL = re.compile(r"\s*(?:'([^'\\\n]*)'|\"([^\"\\\n]*)\")\s*")


def _blank_comments(text: str) -> str:
    """`{# ... #}` becomes spaces, keeping newlines, so positions and line numbers stay."""
    return _JINJA_COMMENT.sub(lambda match: re.sub(r"[^\n]", " ", match.group()), text)


def _arguments(text: str, start: int) -> Optional[Tuple[str, int]]:
    """(text between the call's parentheses, index after its `)`), or None when it does not close nearby."""
    depth, quote = 1, None
    limit = min(len(text), start + MAX_CALL_CHARS)
    index = start
    while index < limit:
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return text[start:index], index + 1
        index += 1
    return None


def _top_level(body: str) -> List[str]:
    """Arguments split at commas outside brackets and strings."""
    parts, depth, quote, begin = [], 0, None, 0
    index = 0
    while index < len(body):
        char = body[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(body[begin:index])
            begin = index + 1
        index += 1
    parts.append(body[begin:])
    return parts


def config(text: str) -> Optional[dict]:
    """{"line", "alias", "schema", "dynamic", "branched"} for the model's config calls, or None without one.

    `alias` and `schema` hold the last literal value given; a key whose last value
    was not a literal is listed in `dynamic` instead. `line` is the last call's line."""
    if "config" not in text:
        return None
    clean = _blank_comments(text)
    tags = [(match.start(), match.group(1)) for match in _BLOCK_TAG.finditer(clean)]
    values: Dict[str, Optional[str]] = {}
    dynamic: Dict[str, bool] = {}
    line, branched, position = None, False, 0
    depth, seen = 0, 0  # open if/for blocks before the current call, and tags counted so far
    breaks, counted = 0, 0  # newlines before `counted`, so line numbers take one pass
    while True:
        call = _CALL.search(clean, position)
        if call is None:
            break
        found = _arguments(clean, call.end())
        if found is None:
            break
        body, position = found
        breaks += clean.count("\n", counted, call.start())
        counted = call.start()
        line = breaks + 1
        while seen < len(tags) and tags[seen][0] < call.start():
            depth += 1 if tags[seen][1] in ("if", "for") else -1
            seen += 1
        branched = branched or depth > 0
        for part in _top_level(body):
            argument = _ARGUMENT.fullmatch(part)
            if argument is None or argument.group(1) not in KEYS:
                continue
            literal = _LITERAL.fullmatch(argument.group(2))
            key = argument.group(1)
            if literal is None:
                values[key], dynamic[key] = None, True
            else:
                values[key] = literal.group(1) if literal.group(1) is not None else literal.group(2)
                dynamic[key] = False
    if line is None:
        return None
    return {"line": line, "alias": values.get("alias"), "schema": values.get("schema"),
            "dynamic": sorted(key for key, flag in dynamic.items() if flag), "branched": branched}
