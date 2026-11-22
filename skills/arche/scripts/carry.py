#!/usr/bin/env python3
"""Carry map.json and descriptions from an earlier run onto this scan, or save them to share."""
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("arche: Python 3.9 or newer is required\n")
    sys.exit(2)

import argparse
import shutil
from pathlib import Path

from arche_lib import cli
from arche_lib.atlas import load_inputs
from arche_lib.carry import carry
from arche_lib.errors import UsageError
from arche_lib.jsonio import read_json, write_json
from arche_lib.sources import make_reader

SHARED = ("map.json", "map.lock.json")


def _save(out, destination):
    if not (out / "map.lock.json").exists():
        raise UsageError("run map.py before --save")
    destination.mkdir(parents=True, exist_ok=True)
    for name in SHARED:
        if (out / name).exists():
            shutil.copyfile(out / name, destination / name)
    if (out / "descriptions").is_dir():
        (destination / "descriptions").mkdir(exist_ok=True)
        for path in sorted((out / "descriptions").glob("*.json")):
            shutil.copyfile(path, destination / "descriptions" / path.name)
    return {"step": "carry", "saved": str(destination.resolve())}


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="run directory of the current scan (after imports.py)")
    parser.add_argument("--from", dest="source", type=Path, help="directory holding an earlier map.json, map.lock.json and descriptions/")
    parser.add_argument("--save", type=Path, help="copy this run's map.json, map.lock.json and descriptions/ to a directory to share or commit")
    args = parser.parse_args(argv)
    out = args.out
    if args.save is not None:
        return _save(out, args.save)
    if args.source is None:
        raise UsageError("give --from PREVIOUS_RUN, or --save DIRECTORY")
    source = args.source
    if source.resolve() == out.resolve():
        raise UsageError("--from must be a different directory from --out")
    if not (source / "map.lock.json").exists():
        raise UsageError(f"no map.lock.json in {source}; it is written by map.py")
    inventory, graph = load_inputs(out)
    document = read_json(source / "map.json") if (source / "map.json").exists() else None
    folder = source / "descriptions"
    descriptions = {path.name: read_json(path) for path in sorted(folder.glob("*.json"))} if folder.is_dir() else {}
    document, carried, report = carry(document, descriptions, read_json(source / "map.lock.json"), inventory, graph, make_reader(inventory))
    if document is not None:
        write_json(out / "map.json", document)
    if carried:
        (out / "descriptions").mkdir(exist_ok=True)
        for name, content in carried.items():
            write_json(out / "descriptions" / Path(name).name, content)
    write_json(out / "carry.json", report)
    return {"step": "carry", "report": str((out / "carry.json").resolve()),
            **{key: len(value) for key, value in report.items()}}


if __name__ == "__main__":
    cli.run(main)
