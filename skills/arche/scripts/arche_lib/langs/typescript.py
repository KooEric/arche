"""TypeScript and JavaScript adapter."""
from __future__ import annotations

from .ts_extract import extract as extract_imports
from .ts_http import routes
from .ts_requests import requests_and_clients
from .ts_routes import routes_and_mounts
from .ts_resolve import Context, build_context, resolve

__all__ = ["Context", "build_context", "extract", "resolve"]


def extract(path: str, text: str) -> dict:
    result = extract_imports(path, text)
    router_routes, mounts, routers = routes_and_mounts(path, text)
    result["http_routes"] = routes(path, text) + router_routes
    result["http_mounts"] = mounts
    result["http_routers"] = routers
    result["http_requests"], result["http_clients"], result["http_pending_requests"] = requests_and_clients(path, text)
    return result
