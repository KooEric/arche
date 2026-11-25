#!/usr/bin/env python3
"""Scan a repository, resolve its imports and render the report in one command."""
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("arche: Python 3.9 or newer is required\n")
    sys.exit(2)

import argparse
import tempfile
from pathlib import Path

import carry as carry_step
import data_profile
import imports
import map as atlas_map
import render
import scan
from arche_lib import cli
from arche_lib.errors import UsageError
from arche_lib.i18n import LANGUAGES
from arche_lib.paths import arche_home


def _writable_home():
    """The default run directory lives under the home directory, which a sandbox may deny."""
    try:
        (arche_home() / "runs").mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return True


def _profile_args(out, args):
    options = ["--out", out, *[arg for directory in args.data for arg in ("--data", directory)]]
    if args.data_map:
        options += ["--map", args.data_map]
    if args.max_rows is not None:
        options += ["--max-rows", str(args.max_rows)]
    if args.show_values:
        options.append("--show-values")
    return options


def _drop_profile(out):
    try:
        (Path(out) / data_profile.PROFILE_FILE).unlink()
    except FileNotFoundError:
        pass


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", nargs="?", default=".", help="directory to analyze (default: current directory)")
    parser.add_argument("--out", help="output directory outside the repository (default: ~/.arche/runs/<name>-<commit>)")
    parser.add_argument("--from", dest="source", help="earlier run or saved map directory to carry map.json and descriptions from")
    parser.add_argument("--tour", help="tour JSON to validate and include")
    parser.add_argument("--lang", choices=LANGUAGES, default="en", help="language of the report interface (default: en)")
    parser.add_argument("--internal-exports", action="append", default=[], metavar="DIR",
                        help="opt in a repository-relative internal directory for explicit Python export checks")
    parser.add_argument("--jpath", action="append", default=[], metavar="DIR",
                        help="repository-relative Jsonnet library directory, as given to jsonnet -J (repeatable)")
    parser.add_argument("--data", action="append", default=[], metavar="DIR",
                        help="data folder outside the repository to profile and match to tables (repeatable)")
    parser.add_argument("--data-map", metavar="FILE", help="data-map.json naming the dataset of a table")
    parser.add_argument("--max-rows", type=int, help="rows read per dataset when profiling")
    parser.add_argument("--show-values", action="store_true", help="show common values of columns that do not look sensitive")
    args = parser.parse_args(argv)
    if not args.data:
        for flag, given in (("--data-map", args.data_map), ("--max-rows", args.max_rows is not None),
                            ("--show-values", args.show_values)):
            if given:
                raise UsageError(f"{flag} needs --data")

    out = args.out
    if out is None and not _writable_home():
        out = tempfile.mkdtemp(prefix="arche-")
    scanned = scan.main([args.repo, *(["--out", out] if out else [])])
    out = scanned["out"]
    resolved = imports.main(["--out", out, *[arg for directory in args.jpath for arg in ("--jpath", directory)]])
    profiled = data_profile.main(_profile_args(out, args)) if args.data else None
    if not args.data:
        # a profile left by an earlier --data run describes data this run was not given
        _drop_profile(out)
    # Rebuild the atlas for this snapshot: from OUT/map.json when the agent wrote one, else from folders.
    carried = carry_step.main(["--out", out, "--from", args.source]) if args.source else None
    mapped = atlas_map.main(["--out", out])
    options = ["--out", out, "--lang", args.lang]
    if args.tour:
        options += ["--tour", str(Path(args.tour).resolve())]
    for directory in args.internal_exports:
        options += ["--internal-exports", directory]
    rendered = render.main(options)
    return {
        "step": "arche",
        "out": out,
        "report": rendered["report"],
        "map": rendered["map"],
        "readonly_check": rendered["readonly_check"],
        "repo": scanned["repo"],
        "commit": scanned["commit"],
        "analyzed": scanned["analyzed"],
        "skipped": scanned["skipped"],
        "edges": resolved["edges"],
        "map_source": mapped["source"],
        **({"carried": {key: value for key, value in carried.items() if key != "step"}} if carried else {}),
        "map_warnings": mapped["warnings"],
        **({"profile": {key: value for key, value in profiled.items() if key != "step"}} if profiled else {}),
    }


if __name__ == "__main__":
    cli.run(main)
