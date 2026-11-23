"""Resolve a path written in source text to a scanned file. Language independent."""
from __future__ import annotations

import posixpath
import re
from typing import Dict, Iterable, List, Sequence

SUFFIXES = (".sql", ".hql")
MAX_SPEC_CHARS = 200
_TEMPLATE_CHARS = "{%*?"
_JINJA = re.compile(r"\{\{.*?\}\}")


def is_path(text: str) -> bool:
    """True for text that can name one SQL file: right suffix, a stem, no whitespace outside `{{ }}`, not a URL."""
    return (len(SUFFIXES[0]) < len(text) <= MAX_SPEC_CHARS and text.lower().endswith(SUFFIXES)
            and "://" not in text
            and not any(char.isspace() for char in _JINJA.sub("{}", text)))


def is_template(text: str) -> bool:
    """True when part of the path is filled in at run time or is a glob."""
    return any(char in text for char in _TEMPLATE_CHARS)


def index(paths: Iterable[str]) -> Dict[str, List[str]]:
    """File name -> sorted paths carrying that name."""
    by_name: Dict[str, List[str]] = {}
    for path in sorted(paths):
        by_name.setdefault(posixpath.basename(path), []).append(path)
    return by_name


def _normalize(spec: str) -> str:
    spec = spec.replace("\\", "/")
    while spec.startswith("./"):
        spec = spec[2:]
    return spec.lstrip("/")


def _unresolved(spec: str, reason: str, candidates: Sequence[str] = ()) -> dict:
    return {"status": "unresolved", "spec": spec, "reason": reason,
            **({"candidates": list(candidates)} if candidates else {})}


def resolve(spec: str, importer_path: str, by_name: Dict[str, List[str]], dynamic: bool = False) -> dict:
    """verified (rule relative|root), inferred (rule suffix) or unresolved (reason dynamic|ambiguous|not_found)."""
    if dynamic:
        return _unresolved(spec, "dynamic")
    clean = _normalize(spec)
    known = by_name.get(posixpath.basename(clean), [])
    relative = posixpath.normpath(posixpath.join(posixpath.dirname(importer_path), clean))
    if relative in known:
        return {"status": "verified", "to": relative, "rule": "relative"}
    parts = clean.split("/")
    if ".." in parts:
        return _unresolved(spec, "not_found")
    if clean in known:
        return {"status": "verified", "to": clean, "rule": "root"}
    # The longest tail of the written path that ends a scanned path decides. A written
    # directory is kept: only a bare file name is matched by name alone.
    shortest = min(len(parts), 2)
    for start in range(len(parts) - shortest + 1):
        tail = "/".join(parts[start:])
        matches = [path for path in known if path == tail or path.endswith("/" + tail)]
        if len(matches) == 1:
            return {"status": "inferred", "to": matches[0], "rule": "suffix"}
        if matches:
            return _unresolved(spec, "ambiguous", matches)
    return _unresolved(spec, "not_found")
