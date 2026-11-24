#!/usr/bin/env python3
"""Explain why YAML files are not linked: group them by key structure and test the linking rules.

Reads the repository read-only and writes yaml-diagnosis.txt and yaml-diagnosis.json to
--output. Values are never written; only key names, value shapes, folder names and counts.
Pass --out with an arche run directory to also count which YAML files are linked already.
"""
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("arche: Python 3.9 or newer is required\n")
    sys.exit(2)

import argparse
import os
from pathlib import Path

from arche_lib import cli
from arche_lib.errors import UsageError
from arche_lib.inventory import build_inventory
from arche_lib.jsonio import read_json, write_json, write_text
from arche_lib.paths import is_within
from arche_lib.sources import make_reader
from arche_lib.yamldiag import diagnose, render_text


def _linked(run_dir: Path) -> set:
    graph = read_json(run_dir / "graph.json")
    linked = set()
    for edge in graph["edges"]:
        for end in (edge.get("from"), edge.get("to")):
            if isinstance(end, str) and end.startswith("file:") and end.lower().endswith((".yaml", ".yml")):
                linked.add(end[5:])
    return linked


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", help="repository to diagnose")
    parser.add_argument("--output", required=True, help="new directory outside the repository for the report")
    parser.add_argument("--out", help="arche run directory of the same repository, to count linked YAML files")
    args = parser.parse_args(argv)
    root = Path(os.path.realpath(args.repo))
    if not root.is_dir():
        raise UsageError(f"not a directory: {args.repo}")
    output = Path(os.path.realpath(args.output))
    if is_within(root, output):
        raise UsageError("--output must be outside the repository")
    inventory = build_inventory(root)
    parsed = [entry["path"] for entry in inventory["files"] if entry["status"] == "analyzed"]
    every = [entry["path"] for entry in inventory["files"]]
    result = diagnose(parsed, every, make_reader(inventory), _linked(Path(args.out)) if args.out else None)
    write_json(output / "yaml-diagnosis.json", result)
    write_text(output / "yaml-diagnosis.txt", render_text(result))
    return {"step": "diagnose_yaml", "report": str(output / "yaml-diagnosis.txt"),
            "groups": len(result["groups"]), "yaml_files": sum(group["files"] for group in result["groups"])}


if __name__ == "__main__":
    cli.run(main)
