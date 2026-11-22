#!/usr/bin/env python3
"""Write a worksheet and a descriptions file per part, for the agent to fill in."""
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("arche: Python 3.9 or newer is required\n")
    sys.exit(2)

import argparse
import re
from collections import defaultdict
from pathlib import Path

from arche_lib import cli
from arche_lib.atlas import load_inputs
from arche_lib.errors import UsageError
from arche_lib.jsonio import read_json, write_json, write_text


def _slug(part_id):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", part_id) or "part"


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="directory holding atlas.json (run map.py first)")
    args = parser.parse_args(argv)
    out = args.out
    if not (out / "atlas.json").exists():
        raise UsageError("run map.py before describe.py")
    _, graph = load_inputs(out)
    atlas = read_json(out / "atlas.json")
    symbols, uses, used_by = defaultdict(list), defaultdict(set), defaultdict(set)
    for node in graph["nodes"]:
        if node["type"] == "symbol":
            path, name = node["id"][4:].rsplit("#", 1)
            symbols[path].append(f"{name} ({node['kind']}, line {node['line']})")
    for edge in graph["edges"]:
        if edge["to"]:
            uses[edge["from"][5:]].add(edge["to"][5:])
            used_by[edge["to"][5:]].add(edge["from"][5:])
    routes = defaultdict(list)
    for route in atlas["routes"]:
        routes[route["file"]].append(route)

    by_part = defaultdict(list)
    for item in atlas["files"]:
        by_part[item["part"] or "unassigned"].append(item)
    labels = {part["id"]: part["label"] for part in atlas["parts"]}
    (out / "worksheets").mkdir(exist_ok=True)
    (out / "descriptions").mkdir(exist_ok=True)
    empty = []
    for part_id in sorted(by_part):
        target = out / "descriptions" / f"{_slug(part_id)}.json"
        filled = read_json(target) if target.exists() else {}
        sheet = [f"# {labels.get(part_id, part_id)}", "",
                 "Give each key a short `name` and a one-line `description` of what the code does today.",
                 "Read the file before you describe it. Leave an entry empty when you have not read it.", ""]
        for item in by_part[part_id]:
            path = item["path"]
            keys = [path] + [f"{route['method']} {route['path']}" for route in routes[path]]
            sheet += [f"## {path}", f"- {item['lang']}, {item['lines']} lines",
                      "- symbols: " + (", ".join(symbols[path][:40]) or "none"),
                      "- uses: " + (", ".join(sorted(uses[path])[:20]) or "none"),
                      "- used by: " + (", ".join(sorted(used_by[path])[:20]) or "none")]
            sheet += [f"- endpoint: {route['method']} {route['path']} (line {route['line']})" for route in routes[path]]
            sheet.append("")
            for key in keys:
                entry = filled.get(key) if isinstance(filled.get(key), dict) else {}
                filled[key] = {"name": str(entry.get("name") or ""), "description": str(entry.get("description") or "")}
                if not filled[key]["description"]:
                    empty.append(key)
        write_text(out / "worksheets" / f"{_slug(part_id)}.md", "\n".join(sheet))
        write_json(target, filled)
    return {"step": "describe", "worksheets": str((out / "worksheets").resolve()),
            "descriptions": str((out / "descriptions").resolve()), "empty": len(empty), "first_empty": empty[:20]}


if __name__ == "__main__":
    cli.run(main)
