"""Match datasets to the table names the scanned SQL reads and writes."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, List

from ..errors import IntegrityError, UsageError
from ..jsonio import read_json

MAP_VERSION = 1
_STATUS_ORDER = {"verified": 0, "inferred": 1}


def load_map(path: Path, root_ids: List[str], dataset_ids: Iterable[str]) -> Dict[str, List[str]]:
    """data-map.json -> {dataset id: [tables]}. A wrong key or path is a UsageError naming it."""
    try:
        data = read_json(path)
    except (IntegrityError, OSError, UnicodeDecodeError) as exc:
        raise UsageError(f"cannot read the data map {path}: {exc}") from None
    if not isinstance(data, dict) or data.get("version") != MAP_VERSION or not isinstance(data.get("tables"), dict):
        raise UsageError('the data map must be {"version": 1, "tables": {"schema.table": "d0:path"}}')
    known = set(dataset_ids)
    mapped: Dict[str, List[str]] = {}
    for table, target in sorted(data["tables"].items()):
        if not isinstance(table, str) or not isinstance(target, str) or not table.strip():
            raise UsageError(f"data map entry {table!r} must map a table name to a dataset path")
        root, sep, rel = target.partition(":")
        if not sep:
            if len(root_ids) != 1:
                raise UsageError(f"data map entry {table!r} must start with a data folder id such as d0:")
            root, rel = root_ids[0], target
        if os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
            raise UsageError(f"data map entry {table!r} must name a path inside its data folder")
        dataset = f"{root}:{rel.strip('/') or '.'}"
        if dataset not in known:
            raise UsageError(f"data map entry {table!r} names {dataset}, which is not a dataset found in the data folders")
        mapped.setdefault(dataset, []).append(table.strip().lower())
    return mapped


def name_parts(record: dict, root_names: Dict[str, str]) -> List[str]:
    """Lower-case name pieces of a dataset path: a/b/c.csv and a/b.c.csv both give [a, b, c]."""
    path = record["path"]
    if path == ".":
        return [root_names[record["root"]].lower()]
    pieces = path.lower().split("/")
    last = pieces[-1]
    if record["kind"] == "file":
        last = os.path.splitext(last)[0]
    return pieces[:-1] + [part for part in last.split(".") if part]


def _match_one(parts: List[str], tables: set, by_last: Dict[str, List[str]]) -> dict:
    for count in (3, 2):
        if len(parts) >= count and ".".join(parts[-count:]) in tables:
            return {"status": "inferred", "rule": "path", "tables": [".".join(parts[-count:])]}
    candidates = by_last.get(parts[-1], [])
    if len(candidates) == 1:
        return {"status": "inferred", "rule": "table_name", "tables": candidates}
    if candidates:
        return {"status": "unresolved", "reason": "ambiguous", "candidates": candidates}
    return {"status": "orphan"}


def match(datasets: List[dict], tables: Iterable[str], mapped: Dict[str, List[str]],
          root_names: Dict[str, str]) -> dict:
    """{"matches", "unresolved", "orphan_datasets", "tables_without_data"}, each sorted."""
    known = sorted(set(tables))
    table_set = set(known)
    by_last: Dict[str, List[str]] = {}
    for table in known:
        by_last.setdefault(table.rsplit(".", 1)[-1], []).append(table)
    matches, unresolved, orphans = [], [], []
    for record in datasets:
        if record["id"] in mapped:
            matches.extend({"table": table, "dataset": record["id"], "status": "verified", "rule": "map"}
                           for table in mapped[record["id"]])
            continue
        found = _match_one(name_parts(record, root_names), table_set, by_last)
        if found["status"] == "inferred":
            matches.append({"table": found["tables"][0], "dataset": record["id"], "status": "inferred", "rule": found["rule"]})
        elif found["status"] == "unresolved":
            unresolved.append({"dataset": record["id"], "reason": found["reason"], "candidates": found["candidates"]})
        else:
            orphans.append(record["id"])
    matches.sort(key=lambda item: (item["table"], _STATUS_ORDER[item["status"]], item["dataset"]))
    matched = {item["table"] for item in matches}
    return {
        "matches": matches,
        "unresolved": sorted(unresolved, key=lambda item: item["dataset"]),
        "orphan_datasets": sorted(orphans),
        "tables_without_data": [table for table in known if table not in matched],
    }
