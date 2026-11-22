"""Python adapter: the three functions every language adapter provides."""
from __future__ import annotations

from .python_extract import extract as extract_imports
from .python_http import routes
from .python_resolve import Context, build_context, resolve

__all__ = ["Context", "build_context", "extract", "resolve"]


def extract(path: str, text: str) -> dict:
    result = extract_imports(path, text)
    result["http_routes"] = routes(path, text)
    return result
