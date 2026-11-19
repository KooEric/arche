#!/usr/bin/env python3
"""Compare two commits and write changes.json using a matching graph snapshot."""
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("arche: Python 3.9 or newer is required\n")
    sys.exit(2)

import argparse
from pathlib import Path

from arche_lib import cli
from arche_lib.changes import build_changes
from arche_lib.errors import IntegrityError, UsageError
from arche_lib.paths import is_within
from arche_lib.jsonio import read_json, write_json


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="run directory containing inventory.json and graph.json")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    out = Path(args.out)
    inventory = read_json(out / "inventory.json")
    graph = read_json(out / "graph.json")
    try:
        root = Path(inventory["repo"]["root"])
        if is_within(root, out):
            raise UsageError("changes output must be outside the scanned repository")
        if inventory["repo"]["commit"] != graph["repo"]["commit"] or inventory["repo"]["dirty"] != graph["repo"]["dirty"]:
            raise IntegrityError("inventory and graph snapshots do not match")
    except (KeyError, TypeError) as exc:
        raise IntegrityError(f"malformed snapshot: {exc!r}") from None
    changes = build_changes(root, graph, args.base, args.head)
    write_json(out / "changes.json", changes)
    return {"step": "changes", "out": str(out), "files": len(changes["files"]),
            "impact_available": changes["impact_available"]}


if __name__ == "__main__":
    cli.run(main)
