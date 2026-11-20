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


def request_path(value: str) -> Optional[Tuple[str, bool, Optional[str]]]:
    """Return (path, dynamic, host) for a URL literal, or None when it has no path.

    One leading interpolation is read as an unknown host (`${baseUrl}/items`).
    `host` is set only for a literal absolute URL.
    """
    named = None
    host = _HOST.match(value)
    if host:
        value = value[host.end():]
    elif value.startswith(("http://", "https://")):
        try:
            parts = urlsplit(value)
        except ValueError:
            return None
        named, value = parts.hostname, parts.path or "/"
    elif not value.startswith("/"):
        return None
    value = re.split(r"[?#]", value, maxsplit=1)[0]
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


def connect(requests: List[dict], routes: List[dict]) -> List[dict]:
    """One connection per request. Exact matches hide pattern matches."""
    by_method: Dict[str, List[dict]] = defaultdict(list)
    for route in routes:
        by_method[route["method"]].append(route)
    connections = []
    for request in requests:
        if "host" in request:  # a named outside host is never one of this repository's routes
            connections.append({"request": request, "candidates": [],
                                "reason": "external_host", "status": "unresolved"})
            continue
        found: Dict[str, List[dict]] = {"exact": [], "pattern": []}
        for route in by_method[request["method"]] + by_method["ALL"]:
            kind = compare(request["path"], route["path"])
            if kind:
                found[kind].append(route)
        candidates = found["exact"] or found["pattern"]
        if len(candidates) > 1:
            reason = "multiple_handlers"
        elif found["exact"]:
            reason = "method_and_path"
        elif candidates:
            reason = "method_and_path_pattern"
        else:
            reason = "dynamic_request_path" if request["dynamic"] else "no_literal_handler"
        connections.append({
            "request": request, "candidates": candidates, "reason": reason,
            "status": "inferred" if len(candidates) == 1 else "unresolved",
        })
    return connections
