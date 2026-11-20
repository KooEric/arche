"""HTTP requests made with fetch or an axios-style client, with a literal URL."""
from __future__ import annotations

import re

from arche_lib.httpmatch import request_path, request_record
from arche_lib.lexer import JS, line_of, mask

_IDENT = r"[A-Za-z_$][\w$]*"
_CLIENT = re.compile(r"\b(?:const|let|var)\s+(" + _IDENT + r")\s*=\s*axios\s*\.\s*create\s*\(")
_FETCH = re.compile(r"(?<![\w$.])fetch\s*\(\s*")
_VERB = re.compile(r"(?<![\w$.])(" + _IDENT + r")\s*\.\s*(get|post|put|patch|delete|head|options)\s*(?:<[^;=()]*>)?\s*\(\s*")
_METHOD = re.compile(r"\bmethod\s*:\s*")


def _call_end(text, start):
    depth = 0
    for pos in range(start, len(text)):
        if text[pos] in "([{":
            depth += 1
        elif text[pos] in ")]}":
            depth -= 1
            if depth < 0:
                return pos
    return len(text)


def requests(path: str, text: str) -> list:
    masked = mask(text, JS)
    clients = {"axios"} | {m.group(1) for m in _CLIENT.finditer(masked.text)}
    found = []

    def add(match, method, url_at):
        body = masked.strings.get(url_at)
        value = request_path(body) if body is not None else None
        if value is not None:
            found.append(request_record(path, line_of(masked, match.start()), method, value))

    for match in _FETCH.finditer(masked.text):
        method = "GET"
        option = _METHOD.search(masked.text, match.end(), _call_end(masked.text, match.end()))
        if option and option.end() in masked.strings:
            method = masked.strings[option.end()].upper()
        add(match, method, match.end())
    for match in _VERB.finditer(masked.text):
        if match.group(1) in clients:
            add(match, match.group(2).upper(), match.end())
    return sorted(found, key=lambda item: item["line"])
