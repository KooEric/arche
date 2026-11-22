#!/usr/bin/env python3
"""Write tour evidence: one step for a line range, or a tour re-anchored to the current scan."""
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("arche: Python 3.9 or newer is required\n")
    sys.exit(2)

import argparse
from pathlib import Path

from arche_lib import cli
from arche_lib.errors import IntegrityError, UsageError
from arche_lib.jsonio import read_json, read_snapshot, write_json
from arche_lib.paths import is_within
from arche_lib.sources import make_reader
from arche_lib.tour import range_digest, refresh_tour, validate_tour


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="directory holding inventory.json")
    parser.add_argument("--path", help="repository-relative file to cite")
    parser.add_argument("--start", type=int, help="first cited line")
    parser.add_argument("--end", type=int, help="last cited line (default: --start)")
    parser.add_argument("--id", help="step id (default: path:start-end)")
    parser.add_argument("--refresh", type=Path, help="existing tour JSON to re-anchor to this scan")
    parser.add_argument("--output", type=Path, help="new file for the re-anchored tour, outside the repository")
    args = parser.parse_args(argv)
    inventory = read_snapshot(args.out / "inventory.json")
    reader = make_reader(inventory)

    if args.refresh is not None:
        if args.output is None or args.output.exists() or is_within(Path(inventory["repo"]["root"]), args.output):
            raise UsageError("--refresh needs --output naming a new file outside the repository")
        tour, moved = refresh_tour(read_json(args.refresh), inventory, reader)
        validate_tour(tour, inventory, reader)
        write_json(args.output, tour)
        return {"step": "cite", "tour": str(args.output.resolve()), "reanchored": moved}

    if args.path is None or args.start is None:
        raise UsageError("give --path and --start, or --refresh and --output")
    end = args.start if args.end is None else args.end
    entry = next((item for item in inventory["files"] if item["path"] == args.path), None)
    text = reader(args.path) if entry is not None and entry.get("status") != "skipped" else None
    if text is None:
        raise IntegrityError(f"not an analyzed file in this scan: {args.path}")
    step = {"id": args.id or f"{args.path}:{args.start}-{end}", "path": args.path, "start_line": args.start,
            "end_line": end, "sha256": entry["sha256"], "range_sha256": range_digest(text, args.start, end)}
    validate_tour({"version": 1, "steps": [step], "edges": []}, inventory, reader)
    return step


if __name__ == "__main__":
    cli.run(main)
