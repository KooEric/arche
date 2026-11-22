#!/usr/bin/env python3
"""Check map.json against the scan and write atlas.json for the map viewer."""
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("arche: Python 3.9 or newer is required\n")
    sys.exit(2)

import argparse
from pathlib import Path

from arche_lib import cli
from arche_lib.atlas import build_atlas, load_inputs, read_descriptions
from arche_lib.carry import build_lock
from arche_lib.jsonio import read_json, read_snapshot, write_json
from arche_lib.sources import make_reader


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="directory holding inventory.json and graph.json")
    parser.add_argument("--map", type=Path, help="map.json written by the agent (default: OUT/map.json; folders when absent)")
    args = parser.parse_args(argv)
    out = args.out
    inventory, graph = load_inputs(out)
    source = args.map or out / "map.json"
    document = read_json(source) if args.map or source.exists() else None
    changes = read_snapshot(out / "changes.json") if (out / "changes.json").exists() else None
    audit = read_json(out / "audit.json") if (out / "audit.json").exists() else {}
    atlas = build_atlas(inventory, graph, document, read_descriptions(out), changes, audit.get("findings"))
    write_json(out / "atlas.json", atlas)
    # The lock lets carry.py move this map onto a later scan.
    write_json(out / "map.lock.json", build_lock(document, inventory, graph, make_reader(inventory)))
    stats = atlas["stats"]
    return {
        "step": "map", "atlas": str((out / "atlas.json").resolve()), "source": atlas["generated_from"],
        "parts": len(atlas["parts"]), "files_assigned": f"{stats['assigned']}/{stats['files']}",
        "descriptions": f"files {stats['described_files']}/{stats['files']} · routes {stats['described_routes']}/{stats['routes']}",
        "warnings": atlas["warnings"],
    }


if __name__ == "__main__":
    cli.run(main)
