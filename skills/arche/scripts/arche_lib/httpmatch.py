"""Match HTTP requests to routes by method and path shape.

Paths are compared segment by segment. A segment written as `:id`, `{id}`,
`<int:id>`, `*`, `$name` or `${expr}` stands for one variable segment.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

_VARIABLE = re.compile(r"^(?::\w+\??|\{[^{}]+\}|<[^<>]+>|\*)$")
_INTERPOLATION = re.compile(r"\$\{[^{}]*\}|\$[A-Za-z_]\w*")
_HOST = re.compile(r"^(?:\$\{[^{}]+\}|\$[A-Za-z_]\w*)(?=/)")
_AUTHORITY = re.compile(r"(?:https?:)?//((?:\$\{[^{}]*\}|[^/?#])*)")


def request_path(value: str) -> Optional[Tuple[str, bool, Optional[str]]]:
    """Return (path, dynamic, host) for a URL literal, or None when it has no path.

    One leading interpolation is read as an unknown host (`${baseUrl}/items`), and
    so is an absolute URL whose host is interpolated (`https://${host}/items`).
    `host` is set only for a literal host, including a protocol-relative one.
    """
    named = None
    host = _HOST.match(value)
    authority = None if host else _AUTHORITY.match(value)
    if host:
        value = value[host.end():]
    elif authority and (value.startswith("//") or value.startswith(("http://", "https://"))):
        if "$" in authority.group(1):
            value = value[authority.end():] or "/"
        else:
            try:
                parts = urlsplit(value)
            except ValueError:
                return None
            if not parts.hostname:
                return None
            named, value = parts.hostname, parts.path or "/"
    elif not value.startswith("/"):
        return None
    value = re.split(r"[?#]", value, maxsplit=1)[0] or "/"
    return value, bool(_INTERPOLATION.search(value)), named


def request_record(file: str, line: int, method: str, value: Tuple[str, bool, Optional[str]]) -> dict:
    path, dynamic, host = value
    record = {"file": file, "line": line, "method": method, "path": path, "dynamic": dynamic}
    return {**record, "host": host} if host else record


def join_path(prefix: str, path: str) -> str:
    """Put a mount prefix in front of a route path. `/api/*` mounts at `/api`."""
    prefix = prefix.rstrip("/")
    if prefix.endswith("/*"):
        prefix = prefix[:-2]
    return prefix + ("" if path == "/" else path) or "/"


def segments(path: str) -> Tuple[Optional[str], ...]:
    """Literal segments as text, variable segments as None."""
    parts = [part for part in path.split("/") if part]
    return tuple(None if _VARIABLE.match(part) or _INTERPOLATION.search(part) else part
                 for part in parts)


def compare(request: str, route: str) -> Optional[str]:
    """"exact", "pattern" or None. A request variable only fits a route variable."""
    left, right = segments(request), segments(route)
    if len(left) != len(right):
        return None
    kind = "exact"
    for ours, theirs in zip(left, right):
        if theirs is None:
            kind = "pattern"
        elif ours != theirs:
            return None
    return kind


def _variable_only(path: str) -> bool:
    parts = segments(path)
    return bool(parts) and all(part is None for part in parts)


def _reason(request: dict, found: Dict[str, List[dict]]) -> Tuple[List[dict], str]:
    """Candidates and reason. Routes with a known prefix hide those without one."""
    kind = "exact" if found["exact"] else "pattern"
    matched = found[kind]
    known = [route for route in matched if not route.get("prefix_unknown")]
    if matched and not known:
        return matched, "route_prefix_unknown"
    if len(known) > 1:
        return known, "multiple_handlers"
    if known and _variable_only(known[0]["path"]):
        return known, "variable_only_route"
    if known:
        return known, "method_and_path" if kind == "exact" else "method_and_path_pattern"
    return [], "dynamic_request_path" if request["dynamic"] else "no_literal_handler"


def connect(requests: List[dict], routes: List[dict]) -> List[dict]:
    """One connection per request. Exact matches hide pattern matches.

    A dynamic route (a template path) is never matched.
    """
    by_method: Dict[str, List[dict]] = defaultdict(list)
    for route in routes:
        if not route.get("dynamic"):
            by_method[route["method"]].append(route)
    connections = []
    for request in requests:
        if "host" in request:  # a named outside host is never one of this repository's routes
            connections.append({"request": request, "candidates": [],
                                "reason": "external_host", "status": "unresolved"})
            continue
        found: Dict[str, List[dict]] = {"exact": [], "pattern": []}
        method = request["method"]
        pool = by_method[method] + (by_method["ALL"] if method != "ALL" else [])
        for route in pool:
            kind = compare(request["path"], route["path"])
            if kind:
                found[kind].append(route)
        candidates, reason = _reason(request, found)
        inferred = reason in ("method_and_path", "method_and_path_pattern")
        connections.append({
            "request": request, "candidates": candidates, "reason": reason,
            "status": "inferred" if inferred else "unresolved",
        })
    return connections
