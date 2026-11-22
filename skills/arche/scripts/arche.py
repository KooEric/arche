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
import imports
import map as atlas_map
import render
import scan
from arche_lib import cli
from arche_lib.i18n import LANGUAGES
from arche_lib.paths import arche_home


def _writable_home():
    """The default run directory lives under the home directory, which a sandbox may deny."""
    try:
        (arche_home() / "runs").mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return True


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", nargs="?", default=".", help="directory to analyze (default: current directory)")
    parser.add_argument("--out", help="output directory outside the repository (default: ~/.arche/runs/<name>-<commit>)")
    parser.add_argument("--from", dest="source", help="earlier run or saved map directory to carry map.json and descriptions from")
    parser.add_argument("--tour", help="tour JSON to validate and include")
    parser.add_argument("--lang", choices=LANGUAGES, default="en", help="language of the report interface (default: en)")
    parser.add_argument("--internal-exports", action="append", default=[], metavar="DIR",
                        help="opt in a repository-relative internal directory for explicit Python export checks")
    args = parser.parse_args(argv)

    out = args.out
    if out is None and not _writable_home():
        out = tempfile.mkdtemp(prefix="arche-")
    scanned = scan.main([args.repo, *(["--out", out] if out else [])])
    out = scanned["out"]
    resolved = imports.main(["--out", out])
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
    }


if __name__ == "__main__":
    cli.run(main)
