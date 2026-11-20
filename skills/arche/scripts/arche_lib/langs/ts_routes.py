"""Routes registered on a router object: app.get("/path", handler).

Only receivers this file binds to a known router constructor, or declares as a
parameter of a router type, are read. That keeps HTTP clients such as
`api.get("/x", options)` out of the route list.
"""
from __future__ import annotations

import re

from arche_lib.httpmatch import join_path
from arche_lib.lexer import JS, line_of, mask

_IDENT = r"[A-Za-z_$][\w$]*"
_FACTORY = r"(?:Hono|OpenAPIHono|Router|express|fastify|Fastify)"
_BOUND = re.compile(
    r"\b(?:const|let|var)\s+(" + _IDENT + r")\s*(?::[^=;]+)?=\s*(?:await\s+)?(?:new\s+)?"
    r"(?:express\s*\.\s*)?" + _FACTORY + r"\s*(?:<[^;=()]*>)?\s*\(")
_PARAM = re.compile(
    r"[(,]\s*(" + _IDENT + r")\s*:\s*(?:Hono|OpenAPIHono|Router|Express|Application|FastifyInstance)\b")
_CALL = re.compile(
    r"(?<![\w$.])(" + _IDENT + r")\s*\.\s*(get|post|put|patch|delete|options|head|all)\s*\(\s*")
_MOUNT = re.compile(r"(?<![\w$.])(" + _IDENT + r")\s*\.\s*(?:route|use)\s*\(\s*")
_TARGET = re.compile(r"\s*,\s*(" + _IDENT + r")\s*[,)]")


def _literal(masked, text, offset):
    """(body, end offset) of the string that opens at `offset`, if any."""
    body = masked.strings.get(offset)
    if body is None:
        return None
    return body, offset + len(body) + 2


def routers(masked) -> set:
    return ({m.group(1) for m in _BOUND.finditer(masked.text)}
            | {m.group(1) for m in _PARAM.finditer(masked.text)})


def routes_and_mounts(path: str, text: str):
    """Return (routes, mounts, router names). Same-file mount prefixes are already applied.

    A mount whose target is not a router of this file is returned for the graph
    step, which follows the import to the file that owns the router.
    """
    masked = mask(text, JS)
    known = routers(masked)
    found = []
    for match in _CALL.finditer(masked.text):
        literal = _literal(masked, text, match.end())
        if match.group(1) not in known or literal is None:
            continue
        route, end = literal
        if not route.startswith("/") or not masked.text[end:].lstrip().startswith(","):
            continue
        line = line_of(masked, match.start())
        found.append({"file": path, "line": line, "handler_line": line,
                      "method": match.group(2).upper(), "path": route,
                      "dynamic": "${" in route, "router": match.group(1)})
    local, mounts = {}, []
    for match in _MOUNT.finditer(masked.text):
        literal = _literal(masked, text, match.end())
        if match.group(1) not in known or literal is None or not literal[0].startswith("/"):
            continue
        target = _TARGET.match(masked.text, literal[1])
        if not target:
            continue
        if target.group(1) in known:
            local[target.group(1)] = (match.group(1), literal[0])
        else:
            mounts.append({"file": path, "line": line_of(masked, match.start()),
                           "prefix": literal[0], "name": target.group(1)})
    for route in found:
        name, seen = route["router"], set()
        while name in local and name not in seen:
            seen.add(name)
            name, prefix = local[name]
            route["path"] = join_path(prefix, route["path"])
    return found, mounts, sorted(known)
