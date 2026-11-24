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
from arche_lib.graph import build_graph
from arche_lib.jsonio import read_snapshot, write_json
from arche_lib.runlog import record_step
from arche_lib.sources import make_reader

INVENTORY_FILE = "inventory.json"
GRAPH_FILE = "graph.json"


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="directory that holds inventory.json")
    parser.add_argument("--jpath", action="append", default=[], metavar="DIR",
                        help="repository-relative Jsonnet library directory, as given to jsonnet -J (repeatable)")
    return parser


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
    inventory = read_snapshot(out_dir / INVENTORY_FILE)
    try:
        graph = build_graph(inventory, make_reader(inventory), {"jpath": args.jpath})
    except (KeyError, TypeError) as exc:
        raise IntegrityError(f"{INVENTORY_FILE} is malformed: {exc!r}") from None
    write_json(out_dir / GRAPH_FILE, graph)
    record_step(out_dir, "imports", started)
    return _summary(graph, out_dir)


if __name__ == "__main__":
    cli.run(main)
