#!/usr/bin/env python3
"""Step 1: list the repository's files and classify them. Writes inventory.json."""
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("arche: Python 3.9 or newer is required\n")
    sys.exit(2)

import argparse
import os
import time
from collections import Counter
from pathlib import Path

from arche_lib import cli, gitinfo
from arche_lib.errors import UsageError
from arche_lib.inventory import build_inventory
from arche_lib.jsonio import write_json
from arche_lib.paths import default_out_dir, is_within
from arche_lib.runlog import record_step

INVENTORY_FILE = "inventory.json"


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", help="directory to scan")
    parser.add_argument("--out", help="output directory (default: ~/.arche/runs/<name>-<commit>)")
    return parser


def _summary(inventory, out_dir):
    files = inventory["files"]
    skipped = Counter(entry["reason"] for entry in files if entry["status"] == "skipped")
    return {
        "step": "scan",
        "out": str(out_dir),
        "repo": inventory["repo"]["name"],
        "commit": inventory["repo"]["commit"],
        "files": len(files),
        "analyzed": sum(1 for entry in files if entry["status"] == "analyzed"),
        "skipped": dict(sorted(skipped.items())),
    }


def main(argv):
    args = _parser().parse_args(argv)
    root = Path(os.path.realpath(args.repo))
    if not root.is_dir():
        raise UsageError(f"not a directory: {args.repo}")
    started = time.time()
    status_before = gitinfo.status_hash(root)  # taken before anything is written
    inventory = build_inventory(root)
    repo = inventory["repo"]
    if args.out:
        out_dir = Path(os.path.realpath(args.out))
    else:
        out_dir = default_out_dir(repo["name"], repo["commit"], repo["dirty"])
    write_json(out_dir / INVENTORY_FILE, inventory)
    record_step(
        out_dir,
        "scan",
        started,
        extra={"status_before": status_before, "out_inside_repo": is_within(root, out_dir)},
        fresh=True,
    )
    return _summary(inventory, out_dir)


if __name__ == "__main__":
    cli.run(main)
