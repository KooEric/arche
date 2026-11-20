"""Routes registered on a router object: app.get("/path", handler).

Only receivers this file binds to a known router constructor, or declares as a
parameter of a router type, are read. That keeps HTTP clients such as
`api.get("/x", options)` out of the route list.

A router made with `Router()` or received as a parameter is a sub-router: its
routes carry `prefix_unknown` until a mount gives them a prefix. `Hono`,
`OpenAPIHono`, `express()` and `fastify()` apps are roots with an empty prefix.
"""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from arche_lib.httpmatch import join_path
from arche_lib.lexer import JS, line_of, mask

_IDENT = r"[A-Za-z_$][\w$]*"
_FACTORY = r"(Hono|OpenAPIHono|Router|express|fastify|Fastify)"
_BOUND = re.compile(
    r"\b(?:const|let|var)\s+(" + _IDENT + r")\s*(?::[^=;]+)?=\s*(?:await\s+)?(?:new\s+)?"
    r"(?:express\s*\.\s*)?" + _FACTORY + r"\s*(?:<[^;=()]*>)?\s*\(")
_PARAM = re.compile(
    r"[(,]\s*(" + _IDENT + r")\s*:\s*(?:Hono|OpenAPIHono|Router|Express|Application|FastifyInstance)\b")
_CALL = re.compile(
    r"(?<![\w$.])(" + _IDENT + r")\s*\.\s*(get|post|put|patch|delete|options|head|all)\s*\(\s*")
_MOUNT = re.compile(r"(?<![\w$.])(" + _IDENT + r")\s*\.\s*(route|use)\s*\(\s*")
_TARGET = re.compile(r"\s*,\s*(" + _IDENT + r")\s*[,)]")
_COMMA = re.compile(r"\s*,")
_CHAINED = re.compile(r"\s*\.")
_WINDOW = 2000  # constructor arguments longer than this are not read


def _literal(masked, text, offset):
    """(body, end offset) of the string that opens at `offset`, if any."""
    body = masked.strings.get(offset)
    if body is None:
        return None
    return body, offset + len(body) + 2


def _chained(text: str, paren: int) -> bool:
    """True when the constructor call opening at `paren` is followed by `.method`
    (`new Hono().basePath("/api")`), or when its end cannot be found."""
    depth = 0
    for pos in range(paren, min(len(text), paren + _WINDOW)):
        if text[pos] == "(":
            depth += 1
        elif text[pos] == ")":
            depth -= 1
            if not depth:
                return bool(_CHAINED.match(text, pos + 1))
    return True


def routers(masked) -> Dict[str, dict]:
    """Router name -> {"sub": bool}. A name bound as a sub-router anywhere is one."""
    found: Dict[str, dict] = {}
    for match in _BOUND.finditer(masked.text):
        sub = match.group(2) == "Router" or _chained(masked.text, match.end() - 1)
        found[match.group(1)] = {"sub": sub or found.get(match.group(1), {}).get("sub", False)}
    for match in _PARAM.finditer(masked.text):
        found[match.group(1)] = {"sub": True}
    return found


def _outermost(name: str, local: Dict[str, set]) -> Optional[Tuple[str, str]]:
    """(outermost router, prefix collected on the way), or None when the chain
    is ambiguous: a router mounted twice in this file, or a cycle."""
    prefix, seen = "", set()
    while name in local:
        if name in seen or len(local[name]) != 1:
            return None
        seen.add(name)
        name, outer = next(iter(local[name]))
        prefix = join_path(outer, prefix or "/") if prefix else outer
    return name, prefix


def _routes(path, text, masked, known):
    found = []
    for match in _CALL.finditer(masked.text):
        literal = _literal(masked, text, match.end())
        if match.group(1) not in known or literal is None:
            continue
        route, end = literal
        if not route.startswith("/") or not _COMMA.match(masked.text, end):
            continue
        line = line_of(masked, match.start())
        found.append({"file": path, "line": line, "handler_line": line,
                      "method": match.group(2).upper(), "path": route,
                      "dynamic": "${" in route, "router": match.group(1)})
    return found


def _mounts(path, text, masked, known):
    """(same-file mounts as {target: {(router, prefix)}}, mounts of other names)."""
    local: Dict[str, set] = {}
    mounts = []
    for match in _MOUNT.finditer(masked.text):
        literal = _literal(masked, text, match.end())
        if match.group(1) not in known or literal is None or not literal[0].startswith("/"):
            continue
        if match.group(2) == "use" and "*" in literal[0]:
            continue  # `app.use("/me/*", auth)` is middleware, not a mount
        target = _TARGET.match(masked.text, literal[1])
        if not target:
            continue
        if target.group(1) in known:
            local.setdefault(target.group(1), set()).add((match.group(1), literal[0]))
        else:
            mounts.append({"file": path, "line": line_of(masked, match.start()),
                           "prefix": literal[0], "name": target.group(1),
                           "router": match.group(1)})
    return local, mounts


def routes_and_mounts(path: str, text: str):
    """Return (routes, mounts, routers). Same-file mount prefixes are already applied.

    Each route names in `router` the outermost router of its same-file mount
    chain, the one a mount in another file may still prefix. Each mount names
    the same for the router it is made on, with the chain's prefix included in
    `prefix`. A mount whose target is not a router of this file is returned for
    the graph step, which follows the import to the file that owns the router.
    """
    masked = mask(text, JS)
    known = routers(masked)
    found = _routes(path, text, masked, known)
    local, mounts = _mounts(path, text, masked, known)
    routes = []
    for route in found:
        chain = _outermost(route["router"], local)
        if chain is None:
            base = {key: value for key, value in route.items() if key != "router"}
            routes.append({**base, "prefix_unknown": True})
            continue
        top, prefix = chain
        joined = join_path(prefix, route["path"]) if prefix else route["path"]
        flag = {"prefix_unknown": True} if known[top]["sub"] else {}
        routes.append({**route, "path": joined, "router": top, **flag})
    resolved = []
    for mount in mounts:
        chain = _outermost(mount["router"], local)
        if chain is None:
            resolved.append({**mount, "prefix_unknown": True})
            continue
        top, prefix = chain
        resolved.append({**mount, "router": top,
                         "prefix": join_path(prefix, mount["prefix"]) if prefix else mount["prefix"]})
    return routes, resolved, known
