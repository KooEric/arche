"""HTTP requests made with fetch or an axios-style client, with a literal URL."""
from __future__ import annotations

import re
from typing import Optional

from arche_lib.httpmatch import request_path, request_record
from arche_lib.lexer import JS, line_of, mask

_IDENT = r"[A-Za-z_$][\w$]*"
_CLIENT = re.compile(r"\b(?:const|let|var)\s+(" + _IDENT + r")\s*=\s*axios\s*\.\s*create\s*\(")
_FETCH = re.compile(r"(?<![\w$.])fetch\s*\(\s*")
_VERB = re.compile(r"(?<![\w$.])(" + _IDENT + r")\s*\.\s*(get|post|put|patch|delete|head|options)\s*(?:<[^;=()\n]{0,200}>)?\s*\(\s*")
_ARGUMENT_END = re.compile(r"\s*([,)])")
_NEXT = re.compile(r"\s*(\S)")
_KEY = re.compile(r"\s*(?:(method)\b|(" + _IDENT + r"))\s*(:)?\s*")
_PROPERTY_END = re.compile(r"\s*[,}]")
_WINDOW = 2000  # an options object longer than this is not read


def _url_end(masked, url_at: int) -> Optional[int]:
    """Offset just after the URL string, when the argument is that string alone."""
    body = masked.strings.get(url_at)
    if body is None:
        return None
    end = url_at + len(body) + 2
    after = _ARGUMENT_END.match(masked.text, end)
    return after.end() - 1 if after else None


def _property_end(text: str, pos: int, stop: int) -> Optional[int]:
    """Offset of the `,` or `}` ending the property that starts at `pos`."""
    depth = 0
    for index in range(pos, stop):
        char = text[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return index if char == "}" else None
            depth -= 1
        elif char == "," and depth == 0:
            return index
    return None


def _options_method(masked, brace: int) -> Optional[str]:
    """The method of a literal options object, GET without a `method` key, or None.

    Only top-level keys count. A spread, a computed or quoted key, or a `method`
    whose value is not a plain string literal makes the method unreadable.
    """
    text, stop = masked.text, min(len(masked.text), brace + _WINDOW)
    pos, method = brace + 1, "GET"
    while pos < stop:
        if _NEXT.match(text, pos) and _NEXT.match(text, pos).group(1) == "}":
            return method
        key = _KEY.match(text, pos, stop)
        if not key:
            return None
        if key.group(1):
            if not key.group(3):
                return None  # shorthand `{ method }`
            value = masked.strings.get(key.end())
            if value is None or text[key.end()] not in "'\"" or not value.isalpha():
                return None
            if not _PROPERTY_END.match(text, key.end() + len(value) + 2):
                return None
            method = value.upper()
        end = _property_end(text, key.end(), stop)
        if end is None:
            return None
        if text[end] == "}":
            return method
        pos = end + 1
    return None


def _fetch_method(masked, after_url: int) -> Optional[str]:
    if masked.text[after_url] == ")":
        return "GET"
    following = _NEXT.match(masked.text, after_url + 1)
    if not following:
        return None
    if following.group(1) == ")":
        return "GET"
    if following.group(1) == "{":
        return _options_method(masked, following.end() - 1)
    return None


_THIS_VERB = re.compile(r"\bthis\s*\.\s*(" + _IDENT + r")\s*\.\s*(get|post|put|patch|delete|head|options)\s*(?:<[^;=()\n]{0,200}>)?\s*\(\s*")
_FIELD_CLIENT = re.compile(r"(?m)^\s*(?:(?:private|public|protected|readonly|static)\s+)*#?(" + _IDENT + r")\s*=\s*axios\s*\.\s*create\s*\(")
_FIELD_FROM = re.compile(r"\bthis\s*\.\s*(" + _IDENT + r")\s*=\s*(" + _IDENT + r")\s*[;\n]")
_FETCH_NAME = re.compile(r"(?<![\w$.])fetch\s*\(\s*(" + _IDENT + r")\s*(?=[,)])")
_CONSTANT = re.compile(r"\bconst\s+(" + _IDENT + r")\s*(?::[^=\n]+)?=\s*")
_EXPORTED_CLIENT = re.compile(r"\bexport\s+(?:const|let|var)\s+(" + _IDENT + r")\s*=\s*axios\s*\.\s*create\s*\(")
_DEFAULT_EXPORT = re.compile(r"\bexport\s+default\s+(" + _IDENT + r")\s*;?")


def requests(path: str, text: str) -> list:
    return requests_and_clients(path, text)[0]


def requests_and_clients(path: str, text: str):
    """(requests, clients this file exports, calls on names that may be an imported client).

    A call such as `api.get("/x")` counts only when `api` is an axios instance. When
    the name is not created here, the graph step checks the file it is imported from.
    """
    masked = mask(text, JS)
    local = {m.group(1) for m in _CLIENT.finditer(masked.text)}
    clients = {"axios"} | local
    default = _DEFAULT_EXPORT.search(masked.text)
    exported = {name: {"default": False} for name in (m.group(1) for m in _EXPORTED_CLIENT.finditer(masked.text))}
    if default and default.group(1) in local:
        exported[default.group(1)] = {"default": True}
    found, pending = [], []

    def add(match, method, url_at):
        value = request_path(masked.strings[url_at])
        if value is not None:
            found.append(request_record(path, line_of(masked, match.start()), method, value))

    for match in _FETCH.finditer(masked.text):
        end = _url_end(masked, match.end())
        method = _fetch_method(masked, end) if end is not None else None
        if method is not None:
            add(match, method, match.end())
    # fetch(url) where `url` is a constant string of this file, defined once.
    constants = {}
    for match in _CONSTANT.finditer(masked.text):
        if match.end() in masked.strings:
            constants[match.group(1)] = None if match.group(1) in constants else match.end()
    for match in _FETCH_NAME.finditer(masked.text):
        at = constants.get(match.group(1))
        method = _fetch_method(masked, match.end()) if at is not None else None
        value = request_path(masked.strings[at]) if method is not None else None
        if value is not None:
            found.append(request_record(path, line_of(masked, match.start()), method, value))
    # this.api.get("/x"): `api` is a client when the class creates it or takes it from one.
    fields = {m.group(1) for m in _FIELD_CLIENT.finditer(masked.text)}
    taken = {m.group(1): m.group(2) for m in _FIELD_FROM.finditer(masked.text)}
    for match in _THIS_VERB.finditer(masked.text):
        if _url_end(masked, match.end()) is None:
            continue
        field = match.group(1)
        source = taken.get(field, field)
        value = request_path(masked.strings[match.end()])
        if value is None:
            continue
        record = request_record(path, line_of(masked, match.start()), match.group(2).upper(), value)
        if field in fields or source in clients:
            found.append(record)
        elif field in taken:
            pending.append({"client": source, "request": record})
    for match in _VERB.finditer(masked.text):
        if _url_end(masked, match.end()) is None:
            continue
        if match.group(1) in clients:
            add(match, match.group(2).upper(), match.end())
        else:
            value = request_path(masked.strings[match.end()])
            if value is not None:
                pending.append({"client": match.group(1),
                                "request": request_record(path, line_of(masked, match.start()), match.group(2).upper(), value)})
    return sorted(found, key=lambda item: item["line"]), exported, pending
