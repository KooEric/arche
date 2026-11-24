"""Resolve a file or directory path written in a configuration file. Language independent.

A reference is {"spec", "line", "quote", "via", "base", "target", "markers"}:
  base     directory the path is relative to ("" is the repository root)
  target   "file", "dir" or "any"
  markers  file names that stand for a directory, best first (e.g. action.yml)
A directory resolves to its first marker that was parsed, else its first parsed file by name.
A marker that was not parsed (a Dockerfile) cannot be an edge target, so it is passed over.

A reference with "fallback" comes from a file no schema describes. It is tried
against its own directory, then the repository root, then, when it names a
directory, the one parsed file whose path ends with it. A match is inferred (rule
path_value); no match gives None, because such a value may not be a path at all.
"""
from __future__ import annotations

import posixpath
from typing import Dict, FrozenSet, List, NamedTuple, Optional

_DYNAMIC = ("${", "{{", "$(")


class Index(NamedTuple):
    paths: FrozenSet[str]          # every inventory path
    analyzed: FrozenSet[str]       # parsed files: valid edge targets
    children: Dict[str, List[str]]  # directory -> sorted files directly in it
    dirs: FrozenSet[str]           # every directory above an inventory path
    by_name: Dict[str, List[str]]  # file name -> parsed files carrying it


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
    parsed = frozenset(analyzed)
    by_name: Dict[str, List[str]] = {}
    for path in paths:
        if path in parsed:
            by_name.setdefault(posixpath.basename(path), []).append(path)
    return Index(frozenset(paths), parsed, children, frozenset(dirs), by_name)


def _unresolved(ref: dict, reason: str) -> dict:
    return {"status": "unresolved", "spec": ref["spec"], "reason": reason}


def is_dynamic(spec: str) -> bool:
    return any(marker in spec for marker in _DYNAMIC)


def _fallback(ref: dict, index: Index) -> Optional[dict]:
    spec = ref["spec"].replace("\\", "/")
    clean = spec[2:] if spec.startswith("./") else spec
    for base in dict.fromkeys((ref["base"], "")):
        joined = posixpath.normpath(posixpath.join(base, clean))
        if joined in index.analyzed:
            return {"status": "inferred", "to": joined, "rule": "path_value"}
    if "/" in clean and not clean.startswith(("../", "/")):
        matches = [path for path in index.by_name.get(posixpath.basename(clean), []) if path.endswith("/" + clean)]
        if len(matches) == 1:
            return {"status": "inferred", "to": matches[0], "rule": "path_value"}
    return None


def resolve(ref: dict, index: Index) -> Optional[dict]:
    """verified (rule relative|dir) or unresolved (reason dynamic|not_found|target_skipped);
    for a fallback reference, inferred (rule path_value) or None."""
    if ref.get("fallback"):
        return _fallback(ref, index)
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
