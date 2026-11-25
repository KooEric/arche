#!/usr/bin/env python3
"""Optional step after imports: profile local data folders and match them to tables. Writes profile.json."""
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("arche: Python 3.9 or newer is required\n")
    sys.exit(2)

import argparse
import os
import time
from pathlib import Path

from arche_lib import cli
from arche_lib.data import DEFAULT_MAX_ROWS, PROFILE_VERSION
from arche_lib.data.discover import discover
from arche_lib.data.match import load_map, match
from arche_lib.data.parquet_rows import find_reader
from arche_lib.data.profiler import profile_dataset
from arche_lib.errors import UsageError
from arche_lib.jsonio import read_snapshot, write_json
from arche_lib.paths import is_within
from arche_lib.runlog import record_step

PROFILE_FILE = "profile.json"


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="run directory that holds inventory.json and graph.json")
    parser.add_argument("--data", action="append", required=True, metavar="DIR",
                        help="data folder outside the repository (repeatable; the first is d0)")
    parser.add_argument("--map", dest="data_map", metavar="FILE", help="data-map.json naming the dataset of a table")
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS, help=f"rows read per dataset (default: {DEFAULT_MAX_ROWS})")
    parser.add_argument("--show-values", action="store_true",
                        help="add the most common values of columns that do not look sensitive")
    return parser


def _roots(data_dirs, repo_root: Path, out_dir: Path):
    roots = []
    for index, directory in enumerate(data_dirs):
        path = Path(os.path.realpath(directory))
        if not path.is_dir():
            raise UsageError(f"--data must be an existing folder: {directory}")
        if is_within(repo_root, path) or is_within(path, repo_root):
            raise UsageError(f"--data must be outside the repository: {directory}")
        if is_within(out_dir, path) or is_within(path, out_dir):
            raise UsageError(f"--data must not overlap the output directory: {directory}")
        for earlier in roots:
            if is_within(Path(earlier["path"]), path) or is_within(path, Path(earlier["path"])):
                raise UsageError(f"--data folders must not be nested: {directory}")
        roots.append({"id": f"d{index}", "path": str(path), "name": path.name})
    return roots


def _tables(graph: dict) -> list:
    return sorted({item["table"] for item in graph.get("written_tables", [])}
                  | {item["table"] for item in graph.get("external_tables", [])})


def main(argv):
    args = _parser().parse_args(argv)
    if args.max_rows < 1:
        raise UsageError("--max-rows must be 1 or more")
    out_dir = Path(os.path.realpath(args.out))
    started = time.time()
    inventory = read_snapshot(out_dir / "inventory.json")
    if not (out_dir / "graph.json").is_file():
        raise UsageError("graph.json is missing; run imports.py first")
    graph = read_snapshot(out_dir / "graph.json")
    roots = _roots(args.data, Path(inventory["repo"]["root"]), out_dir)
    found, skipped, truncated = [], {}, False
    for root in roots:
        datasets, counts, cut = discover(Path(root["path"]), root["id"])
        found.extend(datasets)
        truncated = truncated or cut
        for reason, count in counts.items():
            skipped[reason] = skipped.get(reason, 0) + count
    mapped = load_map(Path(args.data_map), [root["id"] for root in roots],
                      [dataset.record["id"] for dataset in found]) if args.data_map else {}
    parquet = find_reader() if any(dataset.record["format"] == "parquet" for dataset in found) else None
    profiles = [profile_dataset(dataset, args.max_rows, args.show_values, parquet) for dataset in found]
    matched = match([dataset.record for dataset in found], _tables(graph), mapped,
                    {root["id"]: root["name"] for root in roots})
    document = {
        "profile_version": PROFILE_VERSION,
        "inventory_sha256": graph["inventory_sha256"],
        "options": {"max_rows": args.max_rows, "show_values": args.show_values, "data_map": bool(args.data_map)},
        "roots": roots,
        "datasets": profiles,
        **matched,
        "skipped": dict(sorted(skipped.items())),
        "truncated": truncated,
    }
    write_json(out_dir / PROFILE_FILE, document)
    statuses = [item["status"] for item in profiles]
    record_step(out_dir, "profile", started, extra={"parquet_reader": parquet[0] if parquet else None})
    return {
        "step": "profile", "profile": str(out_dir / PROFILE_FILE), "datasets": len(profiles),
        "profiled": statuses.count("profiled"), "schema_only": statuses.count("schema_only"),
        "errors": statuses.count("error"), "matched_tables": len({item["table"] for item in matched["matches"]}),
        "tables_without_data": len(matched["tables_without_data"]), "orphan_datasets": len(matched["orphan_datasets"]),
        "data_changed_during_profile": any(item.get("reason") == "changed_while_reading" for item in profiles),
    }


if __name__ == "__main__":
    cli.run(main)
