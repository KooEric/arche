#!/usr/bin/env python3
"""Compare two commits and write changes.json using a matching graph snapshot."""
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("arche: Python 3.9 or newer is required\n")
    sys.exit(2)

import argparse
from pathlib import Path

from arche_lib import cli, column_impact
from arche_lib.changes import build_changes
from arche_lib.errors import IntegrityError, UsageError
from arche_lib.paths import is_within
from arche_lib.sqlcol import COLUMNS_VERSION
from arche_lib.jsonio import check_graph, read_json, read_snapshot, write_json


COLUMNS_FILE = "columns.json"


def read_columns(out: Path, graph: dict):
    """columns.json of this snapshot, or None when the run has none."""
    path = out / COLUMNS_FILE
    if not path.is_file():
        return None
    columns = read_json(path)
    if not isinstance(columns, dict) or not isinstance(columns.get("columns"), list) \
            or not isinstance(columns.get("edges"), list):
        raise IntegrityError(f"{path} is malformed; run imports.py again")
    if columns.get("columns_version") != COLUMNS_VERSION:
        raise IntegrityError("columns.json has an unsupported columns_version; run imports.py again")
    if columns.get("inventory_sha256") != graph.get("inventory_sha256"):
        raise IntegrityError("columns.json describes a different snapshot; run imports.py again")
    return columns


def has_sql_files(graph: dict) -> bool:
    nodes = graph.get("nodes")
    return isinstance(nodes, list) and any(isinstance(node, dict) and node.get("type") == "file"
                                           and node.get("lang") == "sql" for node in nodes)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="run directory containing inventory.json and graph.json")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base-out", type=Path, help="optional clean base inventory and graph")
    args = parser.parse_args(argv)
    out = Path(args.out)
    inventory = read_snapshot(out / "inventory.json")
    graph = read_snapshot(out / "graph.json")
    try:
        root = Path(inventory["repo"]["root"])
        if is_within(root, out):
            raise UsageError("changes output must be outside the scanned repository")
        check_graph(inventory, graph)
    except (KeyError, TypeError) as exc:
        raise IntegrityError(f"malformed snapshot: {exc!r}") from None
    columns = read_columns(out, graph)
    base_graph = base_columns = None
    base_predates_columns = False
    if args.base_out:
        base_graph = read_snapshot(args.base_out / "graph.json")
        check_graph(read_snapshot(args.base_out / "inventory.json"), base_graph)
        if columns is not None:
            base_columns = read_columns(args.base_out, base_graph)
            # A base with SQL files but no columns.json was scanned before column lineage existed.
            base_predates_columns = base_columns is None and has_sql_files(base_graph)
            base_columns = base_columns or {"columns": [], "edges": []}
    changes = build_changes(root, graph, args.base, args.head, base_graph, columns, base_columns)
    if base_predates_columns:
        changes["column_impact"] = {**column_impact.empty(available=False), "reason": "base_without_columns"}
    write_json(out / "changes.json", changes)
    return {"step": "changes", "out": str(out), "files": len(changes["files"]),
            "impact_available": changes["impact_available"]}


if __name__ == "__main__":
    cli.run(main)
