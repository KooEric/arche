"""Resolve a file or directory path written in a configuration file. Language independent.

A reference is {"spec", "line", "quote", "via", "base", "target", "markers"}:
  base     directory the path is relative to ("" is the repository root)
  target   "file", "dir" or "any"
  markers  file names that stand for a directory, best first (e.g. action.yml)
A directory resolves to its first marker that was parsed, else its first parsed file by name.
A marker that was not parsed (a Dockerfile) cannot be an edge target, so it is passed over.

A reference with target "tree" is a file, or else a directory standing for every
file under it (resolved as the glob `DIR/**`).

A reference with target "glob" names files by pattern (Terraform `fileset`): it
resolves to the first parsed file the pattern matches, and the result carries the
pattern (`glob`) and how many scanned files match it (`matched`). A reference with
"inferred" gives an inferred result in place of a verified one.

A reference with target "name" is a file name attached to a folder chosen at run
time: `resolve_all` gives the file next to the referring file when there is one,
else every parsed file whose path ends with the name (inferred, one result each,
each carrying `matched`, the number of such files). Nothing is kept when no file
matches. graph.py draws one edge for several matches and links each match to the
code in its own folder.

A reference may also carry "rule" (the rule name a match reports), "dynamic" (the
path is built at run time) and "quiet" (graph.py keeps no edge when it is not found).

A reference with "fallback" comes from a file no schema describes. It is tried
against its own directory, then the repository root, then, when it names a
directory, the one parsed file whose path ends with it. A match is inferred (rule
path_value); no match gives None, because such a value may not be a path at all.
"""
from __future__ import annotations

import posixpath
import re
from bisect import bisect_left
from typing import Dict, FrozenSet, List, NamedTuple, Optional, Tuple

_DYNAMIC = ("${", "{{", "$(")


class Index(NamedTuple):
    paths: FrozenSet[str]          # every inventory path
    analyzed: FrozenSet[str]       # parsed files: valid edge targets
    children: Dict[str, List[str]]  # directory -> sorted files directly in it
    dirs: FrozenSet[str]           # every directory above an inventory path
    by_name: Dict[str, List[str]]  # file name -> parsed files carrying it
    ordered: Tuple[str, ...] = ()  # every inventory path, sorted, for prefix ranges


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
    return Index(frozenset(paths), parsed, children, frozenset(dirs), by_name, tuple(paths))


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
            return {"status": "inferred", "to": joined, "rule": ref.get("rule", "path_value")}
    if "/" in clean and not clean.startswith(("../", "/")):
        matches = [path for path in index.by_name.get(posixpath.basename(clean), []) if path.endswith("/" + clean)]
        if len(matches) == 1:
            return {"status": "inferred", "to": matches[0], "rule": ref.get("rule", "path_value")}
    return None


def glob_pattern(pattern: str) -> "re.Pattern[str]":
    """Terraform/doublestar glob: `*` and `?` stay inside one directory, `**` spans
    directories, `{a,b}` is a choice, `[...]` a character class."""
    out, i = [], 0
    while i < len(pattern):
        char = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "{":
            end = pattern.find("}", i)
            if end < 0:
                out.append(re.escape(char))
            else:
                out.append("(?:" + "|".join(re.escape(part) for part in pattern[i + 1:end].split(",")) + ")")
                i = end
        elif char == "[":
            end = pattern.find("]", i + 1)
            if end < 0:
                out.append(re.escape(char))
            else:
                body = pattern[i + 1:end]
                out.append("[" + ("^" + body[1:] if body.startswith("!") else body).replace("\\", "\\\\") + "]")
                i = end
        else:
            out.append(re.escape(char))
        i += 1
    return re.compile("".join(out) + r"\Z")


def _glob(ref: dict, joined: str, index: Index) -> dict:
    regex = glob_pattern(joined)
    static = re.split(r"[*?{\[]", joined, maxsplit=1)[0]
    matches = []
    for path in index.ordered[bisect_left(index.ordered, static):]:
        if not path.startswith(static):
            break
        if regex.match(path):
            matches.append(path)
    if not matches:
        return _unresolved(ref, "not_found")
    parsed = next((path for path in matches if path in index.analyzed), None)
    if parsed is None:
        return {**_unresolved(ref, "target_skipped"), "glob": joined, "matched": len(matches)}
    return {"status": "verified", "to": parsed, "rule": ref.get("rule", "glob"), "glob": joined, "matched": len(matches)}


def resolve_all(ref: dict, index: Index) -> List[dict]:
    """Results for a reference; several for a target "name", at most one otherwise."""
    if ref["target"] != "name":
        result = resolve(ref, index)
        return [] if result is None else [result]
    spec = ref["spec"].replace("\\", "/").lstrip("/")
    rule = ref.get("rule", "same_name")
    local = posixpath.normpath(posixpath.join(ref["base"], spec))
    if local in index.analyzed:
        return [{"status": "inferred", "to": local, "rule": rule}]
    tail = "/" + spec
    matches = [path for path in index.by_name.get(posixpath.basename(spec), [])
               if path == spec or path.endswith(tail)]
    return [{"status": "inferred", "to": path, "rule": rule, "matched": len(matches)} for path in matches]


def resolve(ref: dict, index: Index) -> Optional[dict]:
    """verified (rule relative|dir|glob) or unresolved (reason dynamic|not_found|target_skipped);
    for a fallback reference, inferred (rule path_value) or None."""
    if ref.get("fallback"):
        return _fallback(ref, index)
    result = _resolve(ref, index)
    if ref.get("inferred") and result["status"] == "verified":
        return {**result, "status": "inferred"}
    return result


def _resolve(ref: dict, index: Index) -> dict:
    spec = ref["spec"]
    if ref.get("dynamic") or is_dynamic(spec):
        return _unresolved(ref, "dynamic")
    joined = posixpath.normpath(posixpath.join(ref["base"], spec.replace("\\", "/")))
    if joined == ".." or joined.startswith("../") or spec.startswith("/"):
        return _unresolved(ref, "not_found")
    joined = "" if joined == "." else joined
    target = ref["target"]
    if target == "glob":
        return _glob(ref, joined, index)
    if target == "tree":
        if joined in index.paths:
            target = "file"
        elif joined in index.dirs:
            return _glob(ref, joined + "/**", index)
        else:
            return _unresolved(ref, "not_found")
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
