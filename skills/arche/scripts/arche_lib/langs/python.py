"""Python adapter: the three functions every language adapter provides."""
from __future__ import annotations

from .python_extract import extract as extract_imports
from .python_http import routes_and_mounts
from .python_resolve import Context, build_context, resolve

__all__ = ["Context", "build_context", "extract", "resolve"]


def extract(path: str, text: str) -> dict:
    result = extract_imports(path, text)
    result["http_routes"], result["http_mounts"], result["http_routers"] = routes_and_mounts(path, text)
    return result
