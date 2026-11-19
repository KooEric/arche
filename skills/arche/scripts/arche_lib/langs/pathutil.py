"""Repository-relative POSIX path helpers shared by the adapters."""
from __future__ import annotations

import posixpath
from typing import Optional, Sequence

MAX_QUOTE_CHARS = 200


def parent(path: str) -> str:
    """Directory of a path; "" is the repository root."""
    return posixpath.dirname(path)


def join_norm(base: str, relative: str) -> Optional[str]:
    """Join and normalize. None when the result would leave the repository."""
    joined = posixpath.normpath(posixpath.join(base, relative))
    if joined == "." or joined == "":
        return ""
    if joined == ".." or joined.startswith("../") or joined.startswith("/"):
        return None
    return joined


def source_line(lines: Sequence[str], lineno: int) -> str:
    """The stripped source line used as an edge's evidence quote."""
    if not 1 <= lineno <= len(lines):
        return ""
    return lines[lineno - 1].strip()[:MAX_QUOTE_CHARS]
