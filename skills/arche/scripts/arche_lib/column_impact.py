"""Column impact for changes.json: seed columns from a change, then walk the column graph downstream.

Seeds come from comparing two columns.json snapshots (`column_diff`) or, with one snapshot, from
every output column a changed SQL file writes (`file`). Value edges (to `col:`) carry the walk on;
usage edges (filter, join_key, group_key, order_key to `table:`) end it in `used_by`."""
from __future__ import annotations

from collections import deque
from typing import Dict, Iterable, List, Optional, Set, Tuple

LIMIT = 5000
USAGE_KINDS = frozenset(("filter", "join_key", "group_key", "order_key"))
_RANK = {"verified": 2, "inferred": 1}
_NAMES = {2: "verified", 1: "inferred", 0: "unresolved"}


def empty(available: bool = False) -> dict:
    return {"available": available, "seeds": [], "affected": [], "used_by": [],
            "affected_files": [], "affected_tables": [], "truncated": False}


def _edges(doc: Optional[dict]) -> List[dict]:
    edges = (doc or {}).get("edges")
    return [edge for edge in edges if isinstance(edge, dict)] if isinstance(edges, list) else []


def _columns(doc: Optional[dict]) -> List[dict]:
    columns = (doc or {}).get("columns")
    if not isinstance(columns, list):
        return []
    return [column for column in columns if isinstance(column, dict) and isinstance(column.get("id"), str)]


def _evidence_path(edge: dict) -> Optional[str]:
    evidence = edge.get("evidence")
    path = evidence.get("path") if isinstance(evidence, dict) else None
    return path if isinstance(path, str) else None


def _is_value(edge: dict) -> bool:
    to, source = edge.get("to"), edge.get("from")
    return isinstance(to, str) and to.startswith("col:") and (source is None or isinstance(source, str))


def _incoming(doc: Optional[dict]) -> Dict[str, List[dict]]:
    """Column id -> value edges into it, in document order."""
    result: Dict[str, List[dict]] = {}
    for edge in _edges(doc):
        if _is_value(edge):
            result.setdefault(edge["to"], []).append(edge)
    return result


def _signature(edges: Iterable[dict]) -> Tuple[tuple, ...]:
    return tuple(sorted((str(edge.get("from") or ""), str(edge.get("kind") or ""), str(edge.get("role") or ""),
                         str(edge.get("expression") or "")) for edge in edges))


def _produced(doc: Optional[dict], incoming: Dict[str, List[dict]]) -> Dict[str, Optional[str]]:
    """Columns SQL produced -> the path that defines them."""
    result: Dict[str, Optional[str]] = {}
    for column in _columns(doc):
        known = column.get("known_from")
        if column["id"] in incoming or (isinstance(known, list) and ("sql_output" in known or "ddl" in known)):
            defined = column.get("defined")
            path = defined.get("path") if isinstance(defined, dict) else None
            result[column["id"]] = path if isinstance(path, str) else None
    for column_id in incoming:
        result.setdefault(column_id, None)
    return {column_id: path if path is not None else _first_path(incoming.get(column_id, ()))
            for column_id, path in result.items()}


def _first_path(edges: Iterable[dict]) -> Optional[str]:
    for edge in edges:
        path = _evidence_path(edge)
        if path is not None:
            return path
    return None


def _seed(column_id: str, change: str, seed: str, path: Optional[str]) -> dict:
    return {"column": column_id, "change": change, "seed": seed, "path": path}


def seeds_from_diff(base_doc: Optional[dict], head_doc: Optional[dict]) -> List[dict]:
    """Columns SQL produced that were removed, added or redefined between two snapshots."""
    base_in, head_in = _incoming(base_doc), _incoming(head_doc)
    base, head = _produced(base_doc, base_in), _produced(head_doc, head_in)
    seeds = [_seed(c, "removed", "column_diff", base[c]) for c in base if c not in head]
    seeds += [_seed(c, "added", "column_diff", head[c]) for c in head if c not in base]
    seeds += [_seed(c, "redefined", "column_diff", head[c]) for c in head
              if c in base and _signature(base_in.get(c, ())) != _signature(head_in.get(c, ()))]
    return sorted(seeds, key=lambda s: (s["column"], s["change"]))


def seeds_from_files(doc: Optional[dict], changed_paths: Iterable[str]) -> List[dict]:
    """Every column a value edge from a changed file writes; the seed path is the first such file."""
    changed = set(changed_paths)
    found: Dict[str, str] = {}
    for edge in _edges(doc):
        path = _evidence_path(edge)
        if _is_value(edge) and path in changed:
            found[edge["to"]] = min(path, found.get(edge["to"], path))
    return [_seed(column_id, "file", "file", found[column_id]) for column_id in sorted(found)]


def _index(edges: Iterable[dict]) -> Dict[str, List[dict]]:
    index: Dict[str, List[dict]] = {}
    for edge in edges:
        source, to = edge.get("from"), edge.get("to")
        if isinstance(source, str) and isinstance(to, str):
            index.setdefault(source, []).append(edge)
    return index


def _target(edge: dict) -> Optional[Tuple[str, str]]:
    to = edge["to"]
    if to.startswith("col:"):
        return "affected", to
    if to.startswith("table:") and edge.get("kind") in USAGE_KINDS:
        return "used_by", to
    return None


def _reach(entries: Dict[str, dict], key: str, depth: int, status: int, edge: dict) -> bool:
    """Record one edge into `key`; True when `key` is new."""
    entry = entries.get(key)
    is_new = entry is None
    if is_new:
        entry = entries[key] = {"depth": depth, "status": status, "kinds": set(), "files": set()}
    entry["status"] = max(entry["status"], status)
    entry["kinds"].add(str(edge.get("kind") or ""))
    path = _evidence_path(edge)
    if path is not None:
        entry["files"].add(path)
    return is_new


def downstream(doc_edges: Iterable[dict], seed_ids: Iterable[str], limit: int = LIMIT) -> dict:
    """Breadth-first walk from the seeds; each column is walked once, so cycles end and time is linear."""
    index = _index(edge for edge in doc_edges if isinstance(edge, dict))
    seeds = frozenset(seed_ids)
    found: Dict[str, Dict[str, dict]] = {"affected": {}, "used_by": {}}
    queue = deque(sorted(seeds))
    truncated = False
    while queue and not truncated:
        node = queue.popleft()
        here = found["affected"].get(node, {"depth": 0, "status": 2})
        for edge in index.get(node, ()):
            target = _target(edge)
            if target is None or target[1] in seeds:
                continue
            group, key = target
            entries = found[group]
            if key not in entries and len(found["affected"]) + len(found["used_by"]) >= limit:
                truncated = True
                break
            reached = min(here["status"], _RANK.get(edge.get("status"), 0))
            if _reach(entries, key, here["depth"] + 1, reached, edge) and group == "affected":
                queue.append(key)
    return {"affected": _listing(found["affected"], "column"), "used_by": _listing(found["used_by"], "table"),
            "truncated": truncated}


def _listing(entries: Dict[str, dict], name: str) -> List[dict]:
    result = []
    for key in sorted(entries):
        entry = entries[key]
        item = {name: key, "kinds": sorted(entry["kinds"]), "status": _NAMES[entry["status"]],
                "files": sorted(entry["files"])}
        if name == "column":
            item = {name: key, "depth": entry["depth"], **{k: v for k, v in item.items() if k != name}}
        result.append(item)
    return result


def _merge(results: List[dict], name: str, exclude: Set[str]) -> List[dict]:
    merged: Dict[str, dict] = {}
    for result in results:
        for item in result:
            key = item[name]
            if key in exclude:
                continue
            old = merged.get(key)
            if old is None:
                merged[key] = dict(item)
                continue
            better = max(_RANK.get(old["status"], 0), _RANK.get(item["status"], 0))
            merged[key] = {**old, "status": _NAMES[better], "kinds": sorted(set(old["kinds"]) | set(item["kinds"])),
                           "files": sorted(set(old["files"]) | set(item["files"]))}
            if "depth" in old:
                merged[key]["depth"] = min(old["depth"], item["depth"])
    return [merged[key] for key in sorted(merged)]


def _tables(docs: Iterable[Optional[dict]]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for doc in docs:
        for column in _columns(doc):
            if isinstance(column.get("table"), str):
                result.setdefault(column["id"], column["table"])
    return result


def _cap(affected: List[dict], used_by: List[dict], limit: int) -> Tuple[List[dict], List[dict], bool]:
    if len(affected) + len(used_by) <= limit:
        return affected, used_by, False
    keep = sorted([(a["depth"], a["column"]) for a in affected])[:limit]
    kept = {column for _, column in keep}
    affected = [a for a in affected if a["column"] in kept]
    return affected, used_by[:max(0, limit - len(affected))], True


def build(head_doc: dict, changed_paths: Iterable[str], base_doc: Optional[dict] = None,
          limit: int = LIMIT) -> dict:
    """The `column_impact` block of changes.json."""
    if base_doc is None:
        seeds = seeds_from_files(head_doc, changed_paths)
        walks = [(head_doc, [s["column"] for s in seeds])]
    else:
        seeds = seeds_from_diff(base_doc, head_doc)
        walks = [(base_doc, [s["column"] for s in seeds if s["change"] == "removed"]),
                 (head_doc, [s["column"] for s in seeds if s["change"] != "removed"])]
    results = [downstream(_edges(doc), ids, limit) for doc, ids in walks if ids]
    exclude = {s["column"] for s in seeds}
    affected = _merge([r["affected"] for r in results], "column", exclude)
    used_by = _merge([r["used_by"] for r in results], "table", set())
    affected, used_by, capped = _cap(affected, used_by, limit)
    tables = _tables((head_doc, base_doc))
    names = {tables[a["column"]] for a in affected if a["column"] in tables}
    names |= {u["table"][len("table:"):] for u in used_by}
    files = {path for item in affected + used_by for path in item["files"]}
    return {"available": True, "seeds": seeds, "affected": affected, "used_by": used_by,
            "affected_files": sorted(files), "affected_tables": sorted(names),
            "truncated": capped or any(r["truncated"] for r in results)}
