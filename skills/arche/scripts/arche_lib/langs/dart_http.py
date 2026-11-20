"""Conservative extraction of Dart HTTP calls with literal Uri.parse paths.

This recognises local URI bindings and direct Uri.parse arguments, not arbitrary
Dart evaluation. Unknown or reassigned expressions are intentionally omitted.
"""
from __future__ import annotations

import re
from bisect import bisect_left
from collections import Counter

from ..httpmatch import request_path, request_record
from ..lexer import DART, line_of, mask

_IDENT = r"[A-Za-z_$][\w$]*"
_CALL = re.compile(r"\b(?:" + _IDENT + r"\s*\.\s*)+(get|post|put|patch|delete|head|options)\s*\(", re.I)
_ASSIGN = re.compile(r"\b(" + _IDENT + r")\s*=(?!=|>)")
_URI = re.compile(r"\s*Uri\s*\.\s*parse\s*\(")
_METHOD = re.compile(r"\)\s*(?:async\s*\*?|sync\s*\*)?\s*$")
_CONTROL = re.compile(r"\b(?:if|for|while|switch|catch|assert)\s*\([^{}]*\)\s*$")
_URI_END = re.compile(r"\s*,?\s*\)")
_ARG_NAME = re.compile(r"\s*(" + _IDENT + r")\s*(?=,|\))")
_ARGUMENT_END = re.compile(r"\s*[,)]")


def requests(path: str, text: str) -> list:
    """Return file, HTTP-call line, method, path and dynamic for known URIs."""
    masked = mask(text, DART)
    code = list(masked.text)
    # The shared lexer exposes interpolation expressions as code. They are
    # still part of strings for this extractor, never independent calls.
    for start, body in masked.strings.items():
        quote_len = 3 if text[start:start + 3] in ("'''", '"""') else 1
        end = min(len(text), start + quote_len + len(body) + quote_len)
        for i in range(start, end):
            if code[i] != '\n':
                code[i] = ' '
    code = ''.join(code)

    def uri_at(offset):
        match = _URI.match(code, offset)
        if not match:
            return None
        quote = match.end()
        while quote < len(text) and text[quote].isspace():
            quote += 1
        if text[quote:quote + 1] == 'r' and quote + 1 in masked.strings:
            quote += 1
        if quote not in masked.strings:
            return None
        quote_len = 3 if text[quote:quote + 3] in ("'''", '"""') else 1
        end = quote + quote_len + len(masked.strings[quote]) + quote_len
        if not _URI_END.match(code, end):
            return None
        return request_path(masked.strings[quote])

    events = [(m.start(), 'assign', m) for m in _ASSIGN.finditer(code)]
    events += [(m.start(), 'call', m) for m in _CALL.finditer(code)]
    events += [(i, char, None) for i, char in enumerate(code) if char in '{}']
    events.sort(key=lambda event: event[0])
    scopes = [({}, False)]
    result = []
    for pos, kind, match in events:
        if kind == '{':
            prefix = code[max(0, pos - 300):pos]
            prefix = re.split(r'[;{}]', prefix)[-1]
            is_method = bool(_METHOD.search(prefix) and not _CONTROL.search(prefix))
            scopes.append(({}, scopes[-1][1] or is_method))
        elif kind == '}':
            if len(scopes) > 1:
                scopes.pop()
        elif not scopes[-1][1]:
            continue
        elif kind == 'assign':
            name = match.group(1)
            value = uri_at(match.end())
            # Declarations shadow outer bindings; reassignment updates the
            # nearest existing binding so stale values are not reused.
            before = code[max(0, pos - 100):pos]
            declaration = bool(re.search(r'\b(?:var|final|const|late|Uri)\s+(?:Uri\s+)?$', before))
            target = scopes[-1][0]
            if not declaration:
                target = next((scope for scope, _ in reversed(scopes) if name in scope), target)
            target[name] = value
        else:
            start = match.end()
            value = uri_at(start)
            if value is None:
                arg = _ARG_NAME.match(code, start)
                if arg:
                    value = next((scope[arg.group(1)] for scope, _ in reversed(scopes)
                                  if arg.group(1) in scope), None)
            if value is not None:
                result.append(request_record(path, line_of(masked, match.start(1)),
                                             match.group(1).upper(), value))
    return sorted(result + _helper_requests(path, text, masked, code), key=lambda item: item['line'])


_HELPER = re.compile(r"\b(" + _IDENT + r")\s*\(\s*String\s+(" + _IDENT + r")\s*[,)]")
_FORWARDED = re.compile(r"^(?:\$\{[^{}]+\}|\$[A-Za-z_]\w*)\$\{?(" + _IDENT + r")\}?$")


def _pairs(code: str, open_char: str, close_char: str) -> dict:
    """Offset of each opening bracket mapped to the offset of the one closing it."""
    pairs, stack = {}, []
    for pos, char in enumerate(code):
        if char == open_char:
            stack.append(pos)
        elif char == close_char and stack:
            pairs[stack.pop()] = pos
    return pairs


_MODIFIER = re.compile(r"\s*(?:async\s*\*?|sync\s*\*)?\s*")
_ARROW_WINDOW = 2000  # an arrow body longer than this is not read


def _body(code: str, paren: int, parens: dict, braces: dict):
    """(start, end) offsets of the helper's own body, or None.

    `paren` is the `(` opening the parameter list. A braced body is returned as
    (open, close); an arrow body as (just after `=>`, the terminating `;`).
    Declarations without a body (`;`) have none.
    """
    close = parens.get(paren)
    if close is None:
        return None
    pos = _MODIFIER.match(code, close + 1).end()
    if code.startswith('{', pos):
        end = braces.get(pos)
        return None if end is None else (pos, end)
    if not code.startswith('=>', pos):
        return None
    start = pos + 2
    depth = 0
    for index in range(start, min(len(code), start + _ARROW_WINDOW)):
        char = code[index]
        if char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
        elif char == ';' and depth == 0:
            return start, index
    return None


_NAME_CALL = re.compile(r"(?<![\w$.])(" + _IDENT + r")\s*\(")


def _helpers(text: str, masked, code: str) -> dict:
    """Helper name -> (method, declaration offset) for helpers declared once."""
    declared = list(_HELPER.finditer(code))
    counts = Counter(match.group(1) for match in declared)
    parens, braces = _pairs(code, '(', ')'), _pairs(code, '{', '}')
    offsets = sorted(masked.strings)
    helpers = {}
    for match in declared:
        if counts[match.group(1)] > 1:
            continue
        block = _body(code, match.group(0).index('(') + match.start(), parens, braces)
        if block is None:
            continue
        inside = offsets[bisect_left(offsets, block[0]):bisect_left(offsets, block[1])]
        forwards = any(found and found.group(1) == match.group(2)
                       for found in (_FORWARDED.match(masked.strings[offset]) for offset in inside))
        methods = {call.group(1).upper() for call in _CALL.finditer(code, block[0], block[1])}
        if forwards and len(methods) == 1:
            helpers[match.group(1)] = (methods.pop(), match.start())
    return helpers


def _helper_requests(path: str, text: str, masked, code: str) -> list:
    """Calls to a same-file helper that appends its first String argument to a host.

    `_getJson('/items')` is a GET of /items when `_getJson(String path, ...)`
    builds `Uri.parse('${base}$path')` and makes exactly one kind of client call.
    A helper name declared more than once in the file is not used.
    """
    helpers = _helpers(text, masked, code)
    result = []
    if not helpers:
        return result
    for call in _NAME_CALL.finditer(code):
        if call.group(1) not in helpers:
            continue
        method, declared = helpers[call.group(1)]
        quote = call.end()  # string bodies are blank in `code`; find the quote in the source
        while quote < len(text) and text[quote].isspace():
            quote += 1
        body = masked.strings.get(quote)
        if call.start() == declared or body is None or text[quote] not in "'\"":
            continue
        quote_len = 3 if text[quote:quote + 3] in ("'''", '"""') else 1
        if not _ARGUMENT_END.match(text, quote + quote_len + len(body) + quote_len):
            continue  # `_get('/items/' + id)` is not a request for /items/
        value = request_path(body)
        if value is not None:
            result.append(request_record(path, line_of(masked, call.start()), method, value))
    return result
