"""Carry a map and its descriptions from one scan to the next, and say what needs another look.

map.py records a lock beside each map: the hash of every cited line range and of every
parsed file. With that lock, citations follow their lines when a file changed elsewhere,
and descriptions of untouched files are kept without being read again.
"""
from __future__ import annotations

import hashlib
from typing import Callable, Iterator, Optional

from .tour import _lines, range_digest

LOCK_VERSION = 1


def evidence_holders(document: dict) -> Iterator[tuple]:
    """(label, object that may carry an "evidence" entry) for every place map.json cites source."""
    for index, part in enumerate(document.get("parts") or ()):
        yield f"parts[{index}]", part
    for index, edge in enumerate(document.get("edges") or ()):
        yield f"edges[{index}]", edge
    for index, flow in enumerate(document.get("flows") or ()):
        for number, step in enumerate((flow or {}).get("steps") or ()):
            yield f"flows[{index}].steps[{number}]", step
    for index, screen in enumerate(document.get("screens") or ()):
        for number, item in enumerate((screen or {}).get("items") or ()):
            yield f"screens[{index}].items[{number}]", item


def _key(evidence: dict) -> str:
    return f"{evidence['path']}:{evidence['start_line']}-{evidence.get('end_line', evidence['start_line'])}"


def build_lock(document: Optional[dict], inventory: dict, graph: dict, read_path: Callable[[str], Optional[str]]) -> dict:
    cited = {}
    for _, holder in evidence_holders(document or {}):
        evidence = holder.get("evidence") if isinstance(holder, dict) else None
        if isinstance(evidence, dict) and isinstance(evidence.get("path"), str) and type(evidence.get("start_line")) is int:
            text = read_path(evidence["path"])
            if text is not None:
                cited[_key(evidence)] = range_digest(text, evidence["start_line"], evidence.get("end_line", evidence["start_line"]))
    parsed = {node["id"][5:] for node in graph["nodes"] if node["type"] == "file"}
    return {
        "schema_version": LOCK_VERSION,
        "evidence": cited,
        "files": {entry["path"]: entry["sha256"] for entry in inventory["files"] if entry["path"] in parsed},
        "routes": sorted({f"{route['method']} {route['path']}" for route in graph.get("http_routes", ())}),
    }


def _find(lines: list, size: int, wanted: str, near: int) -> Optional[int]:
    matches = [first for first in range(1, len(lines) - size + 2)
               if hashlib.sha256("\n".join(lines[first - 1:first - 1 + size]).encode("utf-8")).hexdigest() == wanted]
    return min(matches, key=lambda first: (abs(first - near), first)) if matches else None


def carry(document: Optional[dict], descriptions: dict, lock: dict, inventory: dict, graph: dict,
          read_path: Callable[[str], Optional[str]]):
    """Return (map document, descriptions per file name, report) for the new scan.

    `descriptions` maps each descriptions file name to its content.
    """
    old_files = lock.get("files", {})
    new_files = {entry["path"]: entry["sha256"] for entry in inventory["files"]
                 if entry["path"] in {node["id"][5:] for node in graph["nodes"] if node["type"] == "file"}}
    by_hash = {}
    for path, digest in new_files.items():
        by_hash.setdefault(digest, []).append(path)
    moved = {}
    for path, digest in old_files.items():
        targets = [target for target in by_hash.get(digest, ()) if target not in old_files]
        if path not in new_files and len(targets) == 1:
            moved[path] = targets[0]
    report = {
        "files_new": sorted(path for path in new_files if path not in old_files and path not in moved.values()),
        "files_changed": sorted(path for path in new_files if path in old_files and old_files[path] != new_files[path]),
        "files_moved": [{"from": source, "to": target} for source, target in sorted(moved.items())],
        "files_removed": sorted(path for path in old_files if path not in new_files and path not in moved),
        "evidence_moved": [], "evidence_stale": [],
        "routes_new": [], "routes_removed": [],
    }

    if document is not None:
        for label, holder in evidence_holders(document):
            evidence = holder.get("evidence") if isinstance(holder, dict) else None
            if not isinstance(evidence, dict) or not isinstance(evidence.get("path"), str) or type(evidence.get("start_line")) is not int:
                continue
            start, end = evidence["start_line"], evidence.get("end_line", evidence["start_line"])
            old_path, wanted = evidence["path"], lock.get("evidence", {}).get(_key(evidence))
            path = moved.get(old_path, old_path)
            if path in new_files and old_files.get(old_path) == new_files[path]:
                evidence["path"] = path  # the file is byte for byte what it was
                continue
            text = read_path(path) if path in new_files else None
            first = _find(_lines(text), end - start + 1, wanted, start) if text is not None and wanted else None
            if first is None:
                del holder["evidence"]
                report["evidence_stale"].append({"where": label, "path": old_path, "start_line": start, "end_line": end})
                continue
            evidence.update({"path": path, "start_line": first, "end_line": first + end - start})
            if first != start or path != old_path:
                report["evidence_moved"].append({"where": label, "path": path, "from_line": start, "to_line": first})

    routes = {f"{route['method']} {route['path']}" for route in graph.get("http_routes", ())}
    report["routes_new"] = sorted(routes - set(lock.get("routes", ())))
    report["routes_removed"] = sorted(set(lock.get("routes", ())) - routes)
    carried = {}
    for name, content in descriptions.items():
        kept = {}
        for key, value in (content or {}).items():
            target = moved.get(key, key)
            if target in new_files or key in routes:
                kept[target] = value
        carried[name] = kept
    described = {key for content in carried.values() for key, value in content.items()
                 if isinstance(value, dict) and value.get("description")}
    report["review_descriptions"] = [path for path in report["files_changed"] if path in described]
    return document, carried, report
