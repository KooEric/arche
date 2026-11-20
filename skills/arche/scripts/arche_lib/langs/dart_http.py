"""Conservative extraction of Dart HTTP calls with literal Uri.parse paths.

This recognises local URI bindings and direct Uri.parse arguments, not arbitrary
Dart evaluation. Unknown or reassigned expressions are intentionally omitted.
"""
from __future__ import annotations

import re
from ..httpmatch import request_path, request_record
from ..lexer import DART, line_of, mask

_IDENT = r"[A-Za-z_$][\w$]*"
_CALL = re.compile(r"\b(?:" + _IDENT + r"\s*\.\s*)+(get|post|put|patch|delete|head|options)\s*\(", re.I)
_ASSIGN = re.compile(r"\b(" + _IDENT + r")\s*=(?!=|>)")
_URI = re.compile(r"\s*Uri\s*\.\s*parse\s*\(")
_METHOD = re.compile(r"\)\s*(?:async\s*\*?|sync\s*\*)?\s*$")
_CONTROL = re.compile(r"\b(?:if|for|while|switch|catch|assert)\s*\([^{}]*\)\s*$")


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
        if not re.match(r'\s*,?\s*\)', code[end:]):
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
                arg = re.match(r'\s*(' + _IDENT + r')\s*(?=,|\))', code[start:])
                if arg:
                    value = next((scope[arg.group(1)] for scope, _ in reversed(scopes)
                                  if arg.group(1) in scope), None)
            if value is not None:
                result.append(request_record(path, line_of(masked, match.start(1)),
                                             match.group(1).upper(), value))
    return sorted(result + _helper_requests(path, text, masked, code), key=lambda item: item['line'])


_HELPER = re.compile(r"\b(" + _IDENT + r")\s*\(\s*String\s+(" + _IDENT + r")\s*[,)]")
_FORWARDED = re.compile(r"^(?:\$\{[^{}]+\}|\$[A-Za-z_]\w*)\$\{?(" + _IDENT + r")\}?$")


def _matching(code: str, opening: int, open_char: str, close_char: str):
    """Offset of the bracket closing the one at `opening`, or None."""
    depth = 0
    for pos in range(opening, len(code)):
        if code[pos] == open_char:
            depth += 1
        elif code[pos] == close_char:
            depth -= 1
            if not depth:
                return pos
    return None


_MODIFIER = re.compile(r"\s*(?:async\s*\*?|sync\s*\*)?\s*")


def _body(code: str, paren: int):
    """(start, end) offsets of the helper's own body, or None.

    `paren` is the `(` opening the parameter list. A braced body is returned as
    (open, close); an arrow body as (just after `=>`, the terminating `;`).
    Declarations without a body (`;`) have none.
    """
    close = _matching(code, paren, '(', ')')
    if close is None:
        return None
    pos = _MODIFIER.match(code, close + 1).end()
    if code.startswith('{', pos):
        end = _matching(code, pos, '{', '}')
        return None if end is None else (pos, end)
    if not code.startswith('=>', pos):
        return None
    start = pos + 2
    depth = 0
    for index in range(start, len(code)):
        char = code[index]
        if char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
        elif char == ';' and depth == 0:
            return start, index
    return None


def _helper_requests(path: str, text: str, masked, code: str) -> list:
    """Calls to a same-file helper that appends its first String argument to a host.

    `_getJson('/items')` is a GET of /items when `_getJson(String path, ...)`
    builds `Uri.parse('${base}$path')` and makes exactly one kind of client call.
    """
    helpers = {}
    for match in _HELPER.finditer(code):
        block = _body(code, match.group(0).index('(') + match.start())
        if block is None:
            continue
        forwards = any(found and found.group(1) == match.group(2)
                       for offset, body in masked.strings.items() if block[0] <= offset < block[1]
                       for found in [_FORWARDED.match(body)])
        methods = {call.group(1).upper() for call in _CALL.finditer(code, block[0], block[1])}
        if forwards and len(methods) == 1:
            helpers[match.group(1)] = (methods.pop(), match.start())
    result = []
    for name, (method, declared) in helpers.items():
        for call in re.finditer(r"(?<![\w$.])" + re.escape(name) + r"\s*\(", code):
            quote = call.end()  # string bodies are blank in `code`; find the quote in the source
            while quote < len(text) and text[quote].isspace():
                quote += 1
            body = masked.strings.get(quote)
            value = request_path(body) if call.start() != declared and body is not None else None
            if value is not None:
                result.append(request_record(path, line_of(masked, call.start()), method, value))
    return result
