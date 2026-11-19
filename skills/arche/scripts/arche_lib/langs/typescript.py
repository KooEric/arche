"""TypeScript and JavaScript adapter."""
from __future__ import annotations

from .ts_extract import extract as extract_imports
from .ts_http import routes
from .ts_resolve import Context, build_context, resolve

__all__ = ["Context", "build_context", "extract", "resolve"]


def extract(path: str, text: str) -> dict:
    result = extract_imports(path, text)
    result["http_routes"] = routes(path, text)
    return result
