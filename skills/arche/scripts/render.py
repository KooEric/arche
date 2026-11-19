#!/usr/bin/env python3
"""Render a local HTML report from the scan and import artifacts."""
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("arche: Python 3.9 or newer is required\n")
    sys.exit(2)

import argparse
import re
import time
from pathlib import Path

from arche_lib import cli, gitinfo
from arche_lib.errors import IntegrityError, UsageError
from arche_lib.jsonio import read_json, write_text
from arche_lib.paths import is_within
from arche_lib.report import render
from arche_lib.runlog import RUN_FILE, record_step
from arche_lib.sources import make_reader
from arche_lib.tour import validate_tour

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "report-template.html"


def module_summary(inventory, graph):
    """Count all inventoried paths, including files without an adapter."""
    parsed = {node["id"].removeprefix("file:") for node in graph["nodes"] if node["type"] == "file"}
    modules = {}
    for entry in inventory["files"]:
        path = entry["path"]
        name = path.split("/", 1)[0] if "/" in path else "."
        counts = modules.setdefault(name, {"name": name, "parsed": 0, "excluded": 0})
        counts["parsed" if path in parsed else "excluded"] += 1
    return [modules[name] for name in sorted(modules)]


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="directory holding inventory.json and graph.json")
    parser.add_argument("--tour", type=Path, help="optional tour JSON to validate and include")
    args = parser.parse_args(argv)
    out = args.out
    started = time.time()
    inventory = read_json(out / "inventory.json")
    graph = read_json(out / "graph.json")
    run = read_json(out / RUN_FILE)
    try:
        repo = inventory["repo"]
        root = Path(repo["root"])
        if is_within(root, out):
            raise UsageError("report output must be outside the scanned repository")
        if graph["repo"] != {key: repo[key] for key in ("name", "commit", "dirty")}:
            raise IntegrityError("inventory.json and graph.json describe different snapshots")
        commit = repo["commit"]
        if commit is not None and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
            raise IntegrityError("invalid commit in inventory.json")
        commits = gitinfo.recent_commits(root, commit) if commit else None
        before = run["status_before"]
        after = gitinfo.status_hash(root)
        if before is None:
            check = "unavailable"
        else:
            check = "match" if before == after and commit == gitinfo.head_commit(root) else "changed"
        modules = module_summary(inventory, graph)
        tour = read_json(args.tour) if args.tour is not None else None
        if args.tour is not None:
            validate_tour(tour, inventory, make_reader(inventory))
        changes_path = out / "changes.json"
        changes = read_json(changes_path) if changes_path.exists() else None
        if changes is not None and changes["graph_commit"] != graph["repo"]["commit"]:
            raise IntegrityError("changes.json and graph.json describe different snapshots")
        report = render(TEMPLATE.read_text(encoding="utf-8"), graph, commits, check, modules=modules, tour=tour, changes=changes)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise IntegrityError(f"invalid report inputs: {exc!r}") from None

    path = out / "report.html"
    write_text(path, report)
    record_step(out, "render", started, extra={"status_after": after, "readonly_check": check})
    return {"step": "render", "report": str(path.resolve()), "readonly_check": check}


if __name__ == "__main__":
    cli.run(main)
