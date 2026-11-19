"""Python adapter: the three functions every language adapter provides."""
from __future__ import annotations

from .python_extract import extract
from .python_resolve import Context, build_context, resolve

__all__ = ["Context", "build_context", "extract", "resolve"]
