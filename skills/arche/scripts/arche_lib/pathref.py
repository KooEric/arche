"""Resolve a file or directory path written in a configuration file. Language independent.

A reference is {"spec", "line", "quote", "via", "base", "target", "markers"}:
  base     directory the path is relative to ("" is the repository root)
  target   "file", "dir" or "any"
  markers  file names that stand for a directory, best first (e.g. action.yml)
A directory resolves to its first marker that was parsed, else its first parsed file by name.
A marker that was not parsed (a Dockerfile) cannot be an edge target, so it is passed over.
"""
from __future__ import annotations

import posixpath
from typing import Dict, FrozenSet, List, NamedTuple

_DYNAMIC = ("${", "{{", "$(")


class Index(NamedTuple):
    paths: FrozenSet[str]          # every inventory path
    analyzed: FrozenSet[str]       # parsed files: valid edge targets
    children: Dict[str, List[str]]  # directory -> sorted files directly in it
    dirs: FrozenSet[str]           # every directory above an inventory path


def build_index(paths, analyzed) -> Index:
    paths = sorted(paths)
    children: Dict[str, List[str]] = {}
    dirs = set()
    for path in paths:
        parent = posixpath.dirname(path)
        children.setdefault(parent, []).append(path)
        while parent:
            dirs.add(parent)
            parent = posixpath.dirname(parent)
    return Index(frozenset(paths), frozenset(analyzed), children, frozenset(dirs))


def _unresolved(ref: dict, reason: str) -> dict:
    return {"status": "unresolved", "spec": ref["spec"], "reason": reason}


def is_dynamic(spec: str) -> bool:
    return any(marker in spec for marker in _DYNAMIC)


def resolve(ref: dict, index: Index) -> dict:
    """verified (rule relative|dir) or unresolved (reason dynamic|not_found|target_skipped)."""
    spec = ref["spec"]
    if is_dynamic(spec):
        return _unresolved(ref, "dynamic")
    joined = posixpath.normpath(posixpath.join(ref["base"], spec.replace("\\", "/")))
    if joined == ".." or joined.startswith("../") or spec.startswith("/"):
        return _unresolved(ref, "not_found")
    joined = "" if joined == "." else joined
    target = ref["target"]
    if target in ("file", "any") and joined in index.paths:
        if joined in index.analyzed:
            return {"status": "verified", "to": joined, "rule": "relative"}
        return _unresolved(ref, "target_skipped")
    if target in ("dir", "any") and (joined in index.dirs or joined == ""):
        files = index.children.get(joined, [])
        by_name = {posixpath.basename(path): path for path in files}
        chosen = next((by_name[name] for name in ref.get("markers", ())
                       if name in by_name and by_name[name] in index.analyzed), None)
        if chosen is None:
            chosen = next((path for path in files if path in index.analyzed), None)
        if chosen is not None and chosen in index.analyzed:
            return {"status": "verified", "to": chosen, "rule": "dir"}
        return _unresolved(ref, "target_skipped")
    return _unresolved(ref, "not_found")
