"""Conservative extraction of registered TypeScript OpenAPI routes."""
from __future__ import annotations

import re

from arche_lib.lexer import JS, line_of, mask

_IDENT = r"[A-Za-z_$][\w$]*"
_DECL = re.compile(r"\b(?:const|let|var)\s+(" + _IDENT + r")\s*=\s*createRoute\s*\(\s*\{")
_REGISTER = re.compile(r"(?<![\w$.])" + _IDENT + r"\s*\.\s*openapi\s*\(\s*(" + _IDENT + r")\s*,")
_FIELD = re.compile(r"\s*(method|path)\s*:\s*")


def _end(text, start, opening, closing):
    depth = 0
    for pos in range(start, len(text)):
        if text[pos] == opening:
            depth += 1
        elif text[pos] == closing:
            depth -= 1
            if not depth:
                return pos
    return None


def _fields(masked, start, stop):
    """Read literal method and path only from top-level object properties."""
    text = masked.text
    fields = {}
    pos = start + 1
    while pos < stop:
        match = _FIELD.match(text, pos, stop)
        if match:
            quote = match.end()
            value = masked.strings.get(quote)
            if value is not None:
                end = quote + len(value) + 2
                if end <= stop and text[end:stop].lstrip().startswith((",", "}")):
                    fields.setdefault(match.group(1), []).append((text[quote], value))
        # Skip the whole property, including nested schemas and quoted commas.
        depth = 0
        while pos < stop:
            char = text[pos]
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif char == "," and depth == 0:
                pos += 1
                break
            pos += 1
    return fields


def _scope(text, offset):
    stack = []
    for pos, char in enumerate(text[:offset]):
        if char == "{":
            stack.append(pos)
        elif char == "}" and stack:
            stack.pop()
    return tuple(stack)


def routes(path, text):
    """Return records only for locally declared routes registered with a handler.

    Names declared more than once are ambiguous and intentionally omitted.
    Dynamic template paths are retained as candidates, never as literal paths.
    """
    masked = mask(text, JS)
    declarations = {}
    for match in _DECL.finditer(masked.text):
        start = match.end() - 1
        stop = _end(masked.text, start, "{", "}")
        if stop is None or not re.match(r"\s*\)", masked.text[stop + 1:]):
            continue
        fields = _fields(masked, start, stop + 1)
        methods, paths = fields.get("method", []), fields.get("path", [])
        if len(methods) != 1 or len(paths) != 1:
            continue
        method, route = methods[0][1], paths[0][1]
        if not re.fullmatch(r"[A-Za-z]+", method) or not route.startswith("/"):
            continue
        record = {"file": path, "line": line_of(masked, match.start()),
                  "method": method.upper(), "path": route,
                  "dynamic": paths[0][0] == "`" and "${" in route}
        declarations.setdefault(match.group(1), []).append(
            (record, match.start(), _scope(masked.text, match.start())))
    found = []
    for match in _REGISTER.finditer(masked.text):
        candidates = declarations.get(match.group(1), [])
        if len(candidates) != 1:
            continue
        handler = match.end()
        if not re.match(r"\s*[^\s,)]+", masked.text[handler:]):
            continue
        record, declaration_pos, declaration_scope = candidates[0]
        registration_scope = _scope(masked.text, match.start())
        if declaration_pos > match.start() or registration_scope[:len(declaration_scope)] != declaration_scope:
            continue
        record = dict(record, handler_line=line_of(masked, match.start()))
        found.append(record)
    return found
