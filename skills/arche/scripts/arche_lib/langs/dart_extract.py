"""Dart directives (import, export, part) and top-level symbols, read from masked text.

Dart functions need no keyword, so symbols are recognized at the start of each
top-level statement instead of line by line.
"""
from __future__ import annotations

import re
from bisect import bisect_left
from typing import Iterator, List, Optional, Sequence, Tuple

from ..lexer import CLOSERS, DART, OPENERS, Masked, depth_at, line_of, mask
from .pathutil import source_line

HEAD_CHARS = 400
_DIRECTIVE = re.compile(r"^[ \t]*(import|export|part\s+of|part)\b([^;{}]*);", re.M)
_PREFIX = re.compile(r"\bas\s+(\w+)")
_DEFERRED = re.compile(r"\bdeferred\s+as\b")
_SHOW = re.compile(r"\bshow\s+([\w\s,]+?)(?=\bhide\b|\bshow\b|$)")
_HIDE = re.compile(r"\bhide\s+([\w\s,]+?)(?=\bhide\b|\bshow\b|$)")
_ANNOTATION = re.compile(r"@[\w.]+[ \t]*")
_TYPE = r"[\w<>?,.\[\] ]+?"
_MODIFIED_VARIABLE = re.compile(
    r"(?:external\s+)?(?:static\s+)?(?:late\s+)?(?:final|const|var)\s+(?:" + _TYPE + r"\s+)?(\w+)\s*[=;,]"
)
_VARIABLE_KEYWORD = re.compile(r"(?:external\s+)?(?:late\s+)?(?:final|const|var|late)\b")
_PATTERNS = (
    (re.compile(r"(?:(?:abstract|sealed|base|final|interface|mixin)\s+)*class\s+(\w+)"), "class"),
    (re.compile(r"(?:base\s+)?mixin\s+(\w+)"), "mixin"),
    (re.compile(r"enum\s+(\w+)"), "enum"),
    (re.compile(r"extension\s+type\s+(?:const\s+)?(\w+)"), "extension"),
    (re.compile(r"extension\s+(\w+)(?:<[^{]*?>)?\s+on\b"), "extension"),
    (re.compile(r"typedef\s+(?:" + _TYPE + r"\s+)?(\w+)\s*(?:<[^=(]*>)?\s*[=(]"), "typedef"),
)
_GETTER = re.compile(r"(?:external\s+)?(?:" + _TYPE + r"\s+)?get\s+(\w+)")
_SETTER = re.compile(r"(?:external\s+)?(?:void\s+)?set\s+\w+\s*\(")
_FUNCTION = re.compile(r"(?:external\s+)?(?:" + _TYPE + r"\s+)?(\w+)\s*(?:<[^()]*?>)?\s*\(")
_TYPED_VARIABLE = re.compile(r"(?:external\s+)?" + _TYPE + r"\s+(\w+)\s*[=;,]")
_NOT_DECLARATIONS = re.compile(r"(?:import|export|part|library|extension)\b")
_NOT_FUNCTION_NAMES = frozenset({"Function", "if", "for", "while", "switch", "assert", "return", "get", "set"})


def extract(path: str, text: str) -> dict:
    """Return {"imports", "symbols"} and `degraded: True` when brackets do not balance."""
    masked = mask(text, DART)
    lines = text.splitlines()
    imports = list(_directives(masked, lines))
    if not masked.balanced:
        return {"imports": imports, "symbols": [], "degraded": True}
    return {"imports": imports, "symbols": _symbols(masked)}


# ---------- directives ----------

def _names(pattern: "re.Pattern", body: str) -> List[str]:
    return [name for found in pattern.findall(body) for name in re.findall(r"\w+", found)]


def _attrs(body: str) -> dict:
    prefix = _PREFIX.search(body)
    hidden = _names(_HIDE, body)
    return {
        **({"prefix": prefix.group(1)} if prefix else {}),
        **({"deferred": True} if _DEFERRED.search(body) else {}),
        **({"hide": hidden} if hidden else {}),
    }


def _directives(masked: Masked, lines: Sequence[str]) -> Iterator[dict]:
    offsets = sorted(masked.strings)
    for match in _DIRECTIVE.finditer(masked.text):
        if depth_at(masked, match.start(1)) != 0:
            continue
        kind = "part_of" if " " in " ".join(match.group(1).split()) else match.group(1)
        body_start, body_end = match.span(2)
        body = masked.text[body_start:body_end]
        first = bisect_left(offsets, body_start)
        uris = [masked.strings[o] for o in offsets[first:bisect_left(offsets, body_end)]]
        line = line_of(masked, match.start(1))
        common = {
            "kind": kind, "line": line, "quote": source_line(lines, line),
            "names": _names(_SHOW, body), "attrs": _attrs(body),
            "reexport": kind == "export", "part": kind in ("part", "part_of"),
            "type_only": False, "dynamic": False,
        }
        if not uris:
            yield {**common, "uri": None, "library": body.strip(), "conditional": False}
        for position, uri in enumerate(uris):
            yield {**common, "uri": uri, "library": None, "conditional": position > 0}


# ---------- symbols ----------

def _statement_starts(text: str) -> Iterator[int]:
    """Offsets where a top-level statement begins."""
    depth = 0
    at_start = True
    for index, char in enumerate(text):
        if at_start and depth == 0 and not char.isspace() and char != ";":
            yield index
            at_start = False
        if char in OPENERS:
            depth += 1
        elif char in CLOSERS:
            depth -= 1
            at_start = at_start or (depth == 0 and char == "}")
        elif char == ";" and depth == 0:
            at_start = True


def _skip_annotations(text: str, offset: int) -> int:
    """Offset of the declaration that follows any leading `@name` or `@name(...)` annotations."""
    while text.startswith("@", offset):
        offset = _ANNOTATION.match(text, offset).end()
        if text.startswith("(", offset):
            depth = 0
            while offset < len(text):
                depth += (text[offset] == "(") - (text[offset] == ")")
                offset += 1
                if depth == 0:
                    break
        while offset < len(text) and text[offset].isspace():
            offset += 1
    return offset


def _declaration(head: str) -> Optional[Tuple[str, str]]:
    """(name, kind) declared by a statement that starts with `head`."""
    for pattern, kind in _PATTERNS:
        match = pattern.match(head)
        if match:
            return match.group(1), kind
    if _NOT_DECLARATIONS.match(head) or _SETTER.match(head):
        return None
    variable = _MODIFIED_VARIABLE.match(head)
    if variable:
        return variable.group(1), "variable"
    if _VARIABLE_KEYWORD.match(head):
        return None
    for pattern, kind in ((_GETTER, "function"), (_FUNCTION, "function"), (_TYPED_VARIABLE, "variable")):
        match = pattern.match(head)
        if match and match.group(1) not in _NOT_FUNCTION_NAMES:
            return match.group(1), kind
    return None


def _symbols(masked: Masked) -> List[dict]:
    seen = set()
    symbols: List[dict] = []
    for start in _statement_starts(masked.text):
        offset = _skip_annotations(masked.text, start)
        found = _declaration(masked.text[offset:offset + HEAD_CHARS])
        if found is None or found[0] in seen:
            continue
        name, kind = found
        seen.add(name)
        symbols.append(
            {"name": name, "kind": kind, "line": line_of(masked, offset), "exported": not name.startswith("_")}
        )
    return symbols
