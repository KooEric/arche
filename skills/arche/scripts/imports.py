#!/usr/bin/env python3
"""Step 2: extract imports and top-level symbols. Reads inventory.json, writes graph.json."""
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("arche: Python 3.9 or newer is required\n")
    sys.exit(2)

import argparse
import time
from collections import Counter
from pathlib import Path

from arche_lib import cli
from arche_lib.errors import IntegrityError, UsageError
from arche_lib.graph import build_graph_and_columns
from arche_lib.jsonio import read_snapshot, write_json, write_json_rows
from arche_lib.runlog import record_step
from arche_lib.sqlcol import MAX_EDGES
from arche_lib.sources import make_reader

INVENTORY_FILE = "inventory.json"
GRAPH_FILE = "graph.json"
COLUMNS_FILE = "columns.json"


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="directory that holds inventory.json")
    parser.add_argument("--jpath", action="append", default=[], metavar="DIR",
                        help="repository-relative Jsonnet library directory, as given to jsonnet -J (repeatable)")
    add_column_options(parser)
    return parser


def add_column_options(parser):
    parser.add_argument("--no-columns", action="store_true",
                        help="skip column lineage; columns.json says it was turned off (table lineage stays)")
    parser.add_argument("--max-column-edges", type=int, metavar="N",
                        help=f"stop column lineage at N edges (default: {MAX_EDGES:,}); memory grows with N")


def check_column_options(args):
    if args.max_column_edges is not None:
        if args.no_columns:
            raise UsageError("--max-column-edges cannot be combined with --no-columns")
        if args.max_column_edges < 1:
            raise UsageError("--max-column-edges must be at least 1")


def _summary(graph, out_dir):
    return {
        "step": "imports",
        "out": str(out_dir),
        "nodes": dict(sorted(Counter(node["type"] for node in graph["nodes"]).items())),
        # A reference to a file the scan found but does not parse (a .tpl, a .json) is
        # counted on its own, not as unresolved: nothing is wrong with the reference.
        "edges": dict(sorted(Counter(
            "target_not_parsed" if edge.get("reason") == "target_skipped" else edge["status"]
            for edge in graph["edges"]).items())),
        "externals": len(graph["externals"]),
        "skipped": len(graph["skipped"]),
    }


def main(argv):
    args = _parser().parse_args(argv)
    out_dir = Path(args.out)
    started = time.time()
    for directory in args.jpath:
        if directory.startswith(("/", "\\")) or ".." in directory.replace("\\", "/").split("/"):
            raise UsageError(f"--jpath must be a directory inside the repository, relative to its root: {directory}")
    check_column_options(args)
    inventory = read_snapshot(out_dir / INVENTORY_FILE)
    try:
        graph, columns = build_graph_and_columns(inventory, make_reader(inventory), {"jpath": args.jpath},
                                                 columns=not args.no_columns,
                                                 max_column_edges=args.max_column_edges)
    except (KeyError, TypeError) as exc:
        raise IntegrityError(f"{INVENTORY_FILE} is malformed: {exc!r}") from None
    write_json(out_dir / GRAPH_FILE, graph)
    if columns is not None:
        write_json_rows(out_dir / COLUMNS_FILE, columns)
    elif (out_dir / COLUMNS_FILE).exists():
        (out_dir / COLUMNS_FILE).unlink()  # a columns.json from an earlier scan would not match this graph
    record_step(out_dir, "imports", started)
    summary = _summary(graph, out_dir)
    if columns is not None and columns.get("disabled"):
        summary["columns"] = {"disabled": True}
    elif columns is not None:
        summary["columns"] = {
            "columns": len(columns["columns"]),
            "edges": dict(sorted(Counter(edge["status"] for edge in columns["edges"]).items())),
            "unresolved": len(columns["unresolved"]),
            "statements": dict(sorted(Counter(s["status"] for s in columns["statements"]).items())),
            "truncated": columns["truncated"],
        }
    return summary


if __name__ == "__main__":
    cli.run(main)
