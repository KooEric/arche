"""Match a table a file reads to the files that write it. Language independent."""
from __future__ import annotations

from typing import Dict, Iterable, List, Tuple


def index(writes: Iterable[Tuple[str, str]]) -> dict:
    """(file, table) pairs -> {"files": table -> sorted files, "names": last part -> sorted tables}."""
    files: Dict[str, set] = {}
    for path, table in writes:
        files.setdefault(table, set()).add(path)
    names: Dict[str, List[str]] = {}
    for table in sorted(files):
        names.setdefault(table.rsplit(".", 1)[-1], []).append(table)
    return {"files": {table: sorted(paths) for table, paths in files.items()}, "names": names}


def _differs_by_catalog(one: str, other: str) -> bool:
    """True when one name has three parts, the other two, and the two equal the last two of the three."""
    a, b = one.split("."), other.split(".")
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    return len(longer) == 3 and len(shorter) == 2 and longer[1:] == shorter


def resolve(table: str, dynamic: bool, writers: dict) -> List[dict]:
    """verified (rule name), inferred (rule table_name), unresolved (dynamic|ambiguous) or external."""
    if dynamic:
        return [{"status": "unresolved", "spec": table, "reason": "dynamic"}]
    if "{}" not in table and table in writers["files"]:
        return [{"status": "verified", "to": path, "rule": "name"} for path in writers["files"][table]]
    if "{}" not in table:
        # Names that differ only by catalog share the last part, so only that bucket can match.
        same_last = writers["names"].get(table.rsplit(".", 1)[-1], [])
        prefixed = sorted(name for name in same_last if "{}" not in name and _differs_by_catalog(table, name))
        if len(prefixed) == 1:
            return [{"status": "inferred", "to": path, "rule": "table_name"} for path in writers["files"][prefixed[0]]]
        if prefixed:
            return [{"status": "unresolved", "spec": table, "reason": "ambiguous", "candidates": prefixed}]
    prefix, _, last = table.rpartition(".")
    same_name = writers["names"].get(last, [])
    # A schema that is a run-time value or absent, on either side, can only be matched by the table name.
    matches = same_name if not prefix or "{}" in prefix else [
        name for name in same_name if "{}" in name or "." not in name]
    if len(matches) == 1:
        return [{"status": "inferred", "to": path, "rule": "table_name"} for path in writers["files"][matches[0]]]
    if matches:
        return [{"status": "unresolved", "spec": table, "reason": "ambiguous", "candidates": matches}]
    return [{"status": "external", "table": table}]
