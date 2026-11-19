"""Dart adapter."""
from __future__ import annotations

from .dart_extract import extract as extract_imports
from .dart_http import requests
from .dart_resolve import Context, build_context, resolve

__all__ = ["Context", "build_context", "extract", "resolve"]


def extract(path: str, text: str) -> dict:
    result = extract_imports(path, text)
    result["http_requests"] = requests(path, text)
    return result
