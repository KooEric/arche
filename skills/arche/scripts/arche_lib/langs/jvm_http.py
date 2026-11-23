"""Spring MVC routes declared with mapping annotations in Java and Kotlin.

A class-level `@RequestMapping` gives a prefix (and, with `method =`, a default
method) to the mapping annotations on the methods of that class's own body.
Only literal paths are read: a mapping whose path is a constant or an
expression is left out, and a class prefix that is not literal marks the
routes under it `prefix_unknown`. Declarative clients (`@FeignClient`) call
routes instead of serving them, so their mappings are not routes.
"""
from __future__ import annotations

import re

from ..lexer import line_of, mask

_NAME = r"[A-Za-z_$][\w$]*"
_ANNOTATION = re.compile(rf"(?<![\w$.])@\s*((?:{_NAME}\s*\.\s*)*)({_NAME})")
_CLASS = re.compile(rf"(?<![\w$.:@])(class|interface|object|enum|record)\s+{_NAME}")
_MODIFIERS = re.compile(
    r"\s*(?:(?:public|private|protected|internal|open|abstract|final|sealed|non-sealed|static|strictfp|data|"
    r"inner|enum|annotation|value|inline|fun)\s+)*")
_SHORTCUTS = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
              "DeleteMapping": "DELETE", "PatchMapping": "PATCH"}
_MAPPINGS = frozenset(_SHORTCUTS) | {"RequestMapping"}
_CLIENTS = frozenset({"FeignClient"})
_VERBS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"})
_NAMED = re.compile(rf"\s*({_NAME})\s*=(?!=)")
_OPENERS, _CLOSERS = "([{", ")]}"


def _closing(text, start):
    """Offset of the bracket that closes the one at `start`, or None."""
    depth = 0
    for pos in range(start, len(text)):
        if text[pos] in _OPENERS:
            depth += 1
        elif text[pos] in _CLOSERS:
            depth -= 1
            if not depth:
                return pos
    return None


def _split(text, start, stop):
    """(start, stop) of each comma-separated piece at bracket depth 0."""
    pieces, depth, begin = [], 0, start
    for pos in range(start, stop):
        if text[pos] in _OPENERS:
            depth += 1
        elif text[pos] in _CLOSERS:
            depth -= 1
        elif text[pos] == "," and depth == 0:
            pieces.append((begin, pos))
            begin = pos + 1
    pieces.append((begin, stop))
    return [(a, b) for a, b in pieces if text[a:b].strip()]


def _items(text, start, stop):
    """The element ranges of `x`, `{x, y}`, `[x, y]` or `arrayOf(x, y)`."""
    while start < stop and text[start].isspace():
        start += 1
    while stop > start and text[stop - 1].isspace():
        stop -= 1
    opener = re.match(r"arrayOf\s*\(|[{\[]", text[start:stop])
    if opener and _closing(text, start + opener.end() - 1) == stop - 1:
        return _split(text, start + opener.end(), stop - 1)
    return [(start, stop)]


def _strings(masked, ranges, kotlin):
    """The literal strings in `ranges`, or None when any element is not one literal."""
    values = []
    for start, stop in ranges:
        piece = masked.text[start:stop]
        offset = start + len(piece) - len(piece.lstrip())
        value = masked.strings.get(offset)
        if value is None or masked.text[offset + len(value) + 2:stop].strip() or (kotlin and "$" in value):
            return None
        values.append(value)
    return values


def _arguments(masked, start, stop, kotlin):
    """{"paths": [...] or None, "methods": [...] or None} read from annotation arguments."""
    paths, methods = [""], []
    for begin, end in _split(masked.text, start, stop):
        named = _NAMED.match(masked.text, begin, end)
        key, begin = (named.group(1), named.end()) if named else ("value", begin)
        ranges = _items(masked.text, begin, end)
        if key in ("value", "path"):
            paths = _strings(masked, ranges, kotlin)
        elif key == "method":
            verbs = [masked.text[a:b].strip().rsplit(".", 1)[-1].strip() for a, b in ranges]
            methods = verbs if all(verb in _VERBS for verb in verbs) else None
    return {"paths": paths, "methods": methods}


def _groups(masked, kotlin):
    """Runs of consecutive annotations: (annotations, end offset of the run)."""
    text, groups, current, last_end = masked.text, [], [], None
    for match in _ANNOTATION.finditer(text):
        name = match.group(2)
        if name == "interface":
            continue  # `@interface` declares an annotation type
        end = match.end()
        args = None
        opened = re.match(r"\s*\(", text[end:])
        if opened:
            close = _closing(text, end + opened.end() - 1)
            if close is None:
                continue
            args = _arguments(masked, end + opened.end(), close, kotlin)
            end = close + 1
        if current and text[last_end:match.start()].strip():
            groups.append((current, last_end))
            current = []
        current.append({"name": name, "start": match.start(), "args": args})
        last_end = end
    if current:
        groups.append((current, last_end))
    return groups


def _body(text, start):
    """(open, close) of the class body after the declaration at `start`, or None."""
    depth = 0
    for pos in range(start, len(text)):
        char = text[pos]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == ";" and depth == 0:
            return None
        elif char == "{" and depth == 0:
            close = _closing(text, pos)
            return (pos, close) if close is not None else None
    return None


def _join(prefix, path):
    return "/" + "/".join(part.strip("/") for part in (prefix, path) if part.strip("/"))


def _classes(masked, groups):
    """Class bodies with the routing context their own annotations give them."""
    text = masked.text
    by_end = {}
    for annotations, end in groups:
        declared = _MODIFIERS.match(text, end)
        by_end[declared.end()] = annotations
    classes = []
    for match in _CLASS.finditer(text):
        body = _body(text, match.end())
        if body is None:
            continue
        annotations = by_end.get(match.start(), [])
        mapping = next((a for a in annotations if a["name"] == "RequestMapping"), None)
        args = (mapping or {}).get("args") or {"paths": [""], "methods": []}
        classes.append({"open": body[0], "close": body[1],
                        "client": any(a["name"] in _CLIENTS for a in annotations),
                        "prefixes": args["paths"], "methods": args["methods"] or []})
    return classes


def routes(path, text, masked=None):
    """Routes from Spring mapping annotations on methods, sorted by line."""
    kotlin = path.endswith((".kt", ".kts"))
    if masked is None:
        from .jvm import _JAVA, _KOTLIN  # jvm imports this module
        masked = mask(text, _KOTLIN if kotlin else _JAVA)
    groups = _groups(masked, kotlin)
    classes = _classes(masked, groups)
    found = []
    for annotations, end in groups:
        declared = _MODIFIERS.match(masked.text, end)
        if _CLASS.match(masked.text, declared.end()):
            continue  # class-level annotations were read by _classes
        around = [scope for scope in classes if scope["open"] < end < scope["close"]]
        scope = max(around, key=lambda item: item["open"]) if around else None
        if scope and scope["client"]:
            continue
        prefixes = scope["prefixes"] if scope else [""]
        rest = masked.text[end:]
        handler = end + len(rest) - len(rest.lstrip())
        for annotation in annotations:
            if annotation["name"] not in _MAPPINGS:
                continue
            args = annotation["args"] or {"paths": [""], "methods": []}
            if args["paths"] is None or args["methods"] is None:
                continue  # a constant or an expression: not a literal path
            if annotation["name"] in _SHORTCUTS:
                methods = [_SHORTCUTS[annotation["name"]]]
            else:
                methods = args["methods"] or (scope["methods"] if scope else []) or ["ALL"]
            line = line_of(masked, annotation["start"])
            for prefix in prefixes if prefixes is not None else [""]:
                for route in args["paths"] or [""]:
                    for method in methods:
                        found.append({"file": path, "line": line, "handler_line": line_of(masked, handler),
                                      "method": method, "path": _join(prefix, route), "dynamic": False,
                                      **({"prefix_unknown": True} if prefixes is None else {})})
    return sorted(found, key=lambda item: item["line"])
