"""Compare commits and identify reverse import dependencies in a matching graph."""
from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

from . import SCHEMA_VERSION, gitinfo
from .errors import IntegrityError, UsageError


def _commit(root: Path, ref: str) -> str:
    value = gitinfo._git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
    if not value:
        raise UsageError(f"cannot resolve commit: {ref}")
    return value.decode("ascii").strip()


def _diff(root: Path, base: str, head: str) -> list:
    raw = gitinfo._git(root, "diff", "--no-ext-diff", "--no-textconv", "--relative",
                       "--name-status", "-z", "--find-renames", "--diff-filter=ADMRT",
                       base, head, "--", ".")
    if raw is None:
        raise UsageError("cannot compare the requested commits")
    fields = raw.split(b"\0")
    result = []
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i].decode("ascii")[0]
        i += 1
        first = fields[i].decode("utf-8", errors="replace")
        i += 1
        if status == "R":
            path = fields[i].decode("utf-8", errors="replace")
            i += 1
            result.append({"status": "renamed", "path": path, "old_path": first})
        else:
            result.append({"status": {"A": "added", "D": "deleted", "M": "modified", "T": "modified"}[status], "path": first})
    return sorted(result, key=lambda entry: (entry["path"], entry.get("old_path", "")))


_DEPENDENCY_KINDS = ("IMPORTS", "REFERENCES", "READS")


def _reverse_imports(graph: dict) -> dict:
    """File -> files that depend on it directly.

    A Terraform module edge points at one representative file of the module
    directory (`module_dir`); every .tf file in that directory is part of the same
    module, so each of them counts as a dependency of the caller."""
    module_files = defaultdict(list)
    for node in graph["nodes"]:
        if node.get("type") == "file" and node.get("lang") == "terraform":
            path = node["id"][5:]
            module_files[path.rpartition("/")[0]].append(path)
    reverse = defaultdict(set)
    for edge in graph["edges"]:
        source, target = edge.get("from"), edge.get("to")
        if (edge.get("kind") in _DEPENDENCY_KINDS and edge.get("status") in ("verified", "inferred")
                and isinstance(source, str) and source.startswith("file:")
                and isinstance(target, str) and target.startswith("file:")):
            reverse[target[5:]].add(source[5:])
            for member in module_files.get(edge.get("module_dir"), ()):
                reverse[member].add(source[5:])
    return reverse


def _dependents(reverse, seed):
    seen = {seed}
    queue = deque([seed])
    while queue:
        for dependent in reverse.get(queue.popleft(), ()):
            if dependent not in seen:
                seen.add(dependent)
                queue.append(dependent)
    return sorted(seen - {seed})


def _side_impact(item, side, reverse, files):
    path = item.get("old_path", item["path"]) if side == "base" else item["path"]
    if item["status"] == ("added" if side == "base" else "deleted"):
        return {"state": "absent", "paths": []}
    if path not in files:
        return {"state": "unknown", "paths": []}
    return {"state": "analyzed", "paths": _dependents(reverse, path)}


def build_changes(root: Path, graph: dict, base_ref: str, head_ref: str, base_graph=None) -> dict:
    base, head = _commit(root, base_ref), _commit(root, head_ref)
    files = _diff(root, base, head)
    try:
        repo = graph["repo"]
        revision = repo["commit"]
        valid = repo["dirty"] is False and revision in (base, head)
        reverse = _reverse_imports(graph) if valid else {}
    except (KeyError, TypeError, AttributeError) as exc:
        raise IntegrityError(f"graph.json is malformed: {exc!r}") from None
    if base_graph is not None:
        if not valid or revision != head or base_graph["repo"]["commit"] != base or base_graph["repo"]["dirty"] is not False:
            raise UsageError("two-snapshot impact requires clean matching base and head graphs")
        base_reverse = _reverse_imports(base_graph)
        base_files = {n["id"][5:] for n in base_graph["nodes"] if n["type"] == "file"}
        head_files = {n["id"][5:] for n in graph["nodes"] if n["type"] == "file"}
    for item in files:
        seed = item.get("old_path", item["path"]) if revision == base else item["path"]
        absent = (revision == base and item["status"] == "added") or (revision == head and item["status"] == "deleted")
        item["impact"] = _dependents(reverse, seed) if valid and not absent else []
        if base_graph is not None:
            item["comparison"] = {
                "change_evidence": "confirmed",
                "dependency_evidence": "possible",
                "base": _side_impact(item, "base", base_reverse, base_files),
                "head": _side_impact(item, "head", reverse, head_files),
            }
    return {"schema_version": SCHEMA_VERSION, "base": base, "head": head,
            "graph_commit": revision, "impact_available": valid, "files": files}
