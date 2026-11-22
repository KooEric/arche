#!/usr/bin/env python3
"""Write brief.txt: what an agent should read before naming the parts of a repository."""
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("arche: Python 3.9 or newer is required\n")
    sys.exit(2)

import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path

from arche_lib import cli
from arche_lib.atlas import load_inputs
from arche_lib.jsonio import write_text
from arche_lib.sources import make_reader

TREE_DEPTH = 3
TOP = 15
_TEST = re.compile(r"(^|/)(tests?|__tests__|specs?|fixtures)(/|$)|(^|/)(test_[^/]*|[^/]*_test|[^/]*\.(test|spec))\.[^/.]+$")


_ROUTE_LIKE = {
    "python": re.compile(r"^\s*@\w[\w.]*\.(?:get|post|put|patch|delete|route|websocket)\("),
    "typescript": re.compile(r"\.(?:get|post|put|patch|delete|all)\(\s*[\"'`]/"),
}
_ROUTE_LIKE["javascript"] = _ROUTE_LIKE["typescript"]


def unread_routes(graph, read_path):
    """Lines shaped like a route declaration that the extractors did not turn into a route."""
    known = {(route["file"], route["line"]) for route in graph.get("http_routes", ())}
    known |= {(c["request"]["file"], c["request"]["line"]) for c in graph.get("http_connections", ())}
    found = []
    for node in graph["nodes"]:
        pattern = _ROUTE_LIKE.get(node.get("lang")) if node["type"] == "file" else None
        if pattern is None:
            continue
        path = node["id"][5:]
        for number, line in enumerate((read_path(path) or "").splitlines(), 1):
            if pattern.search(line) and (path, number) not in known:
                found.append(f"{path}:{number}")
    return found


def build(graph, read_path=None):
    files = {node["id"][5:]: node for node in graph["nodes"] if node["type"] == "file"}
    lines = [f"REPOSITORY {graph['repo']['name']} · {len(files)} parsed files · {sum(n['lines'] for n in files.values())} lines", ""]

    folders = defaultdict(lambda: [0, 0])
    for path, node in files.items():
        parts = path.split("/")[:-1]
        for depth in range(1, min(len(parts), TREE_DEPTH) + 1):
            entry = folders["/".join(parts[:depth])]
            entry[0] += 1
            entry[1] += node["lines"]
    lines.append(f"CODE TREE (folders to depth {TREE_DEPTH}: files, lines)")
    for folder in sorted(folders):
        lines.append(f"  {'  ' * folder.count('/')}{folder}/  {folders[folder][0]} files, {folders[folder][1]} lines")
    root = sorted(path for path in files if "/" not in path)
    if root:
        lines.append("  (root) " + ", ".join(root))

    fan_in, fan_out, between = Counter(), Counter(), Counter()
    for source, target in {(e["from"][5:], e["to"][5:]) for e in graph["edges"] if e["to"] and e["to"][5:] in files}:
        if source == target:
            continue
        fan_out[source] += 1
        fan_in[target] += 1
        a, b = ("/".join(p.split("/")[:2]) if "/" in p else "." for p in (source, target))
        if a != b:
            between[a, b] += 1
    entries = sorted((p for p in files if fan_out[p] and not fan_in[p] and not _TEST.search(p)), key=lambda p: (-fan_out[p], p))
    lines += ["", "ENTRY POINT CANDIDATES (imported by nothing, importing others; tests left out)"]
    lines += [f"  {path}  imports {fan_out[path]}" for path in entries[:TOP]] or ["  none"]
    lines += ["", "MOST IMPORTED FILES"]
    lines += [f"  {path}  imported by {count}" for path, count in sorted(fan_in.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP]] or ["  none"]
    lines += ["", "IMPORTS BETWEEN FOLDERS (two levels deep)"]
    lines += [f"  {a} -> {b}  {count}" for (a, b), count in sorted(between.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP * 2]] or ["  none"]

    connections = graph.get("http_connections", [])
    lines += ["", f"HTTP ({len(graph.get('http_routes', []))} routes read, {len(connections)} requests read)"]
    for item in connections:
        request = item["request"]
        target = item["candidates"][0]["file"] if item["status"] == "inferred" and item["candidates"] else item["reason"]
        host = f" host {request['host']}" if request.get("host") else ""
        lines.append(f"  {request['method']} {request['path']}{host}  {request['file']}:{request['line']} -> {target}")
    unread = unread_routes(graph, read_path) if read_path else []
    lines += ["", f"ROUTE-LIKE LINES NOT READ ({len(unread)}; check these by hand, the map will not show them)"]
    lines += ["  " + ", ".join(unread[:TOP])] if unread else ["  none"]
    hosts = sorted({item["request"]["host"] for item in connections if item["request"].get("host")})
    packages = Counter(item["package"] for item in graph["externals"])
    lines += ["", "EXTERNAL HOSTS", "  " + (", ".join(hosts) or "none"), "", "EXTERNAL PACKAGES (files using each)"]
    lines += [f"  {name}  {count}" for name, count in sorted(packages.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP * 2]] or ["  none"]
    skipped = Counter(item["reason"] for item in graph["skipped"])
    lines += ["", "NOT PARSED", "  " + (", ".join(f"{reason} {count}" for reason, count in sorted(skipped.items())) or "none")]
    return "\n".join(lines) + "\n"


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="directory holding inventory.json and graph.json")
    args = parser.parse_args(argv)
    inventory, graph = load_inputs(args.out)
    path = args.out / "brief.txt"
    write_text(path, build(graph, make_reader(inventory)))
    return {"step": "brief", "brief": str(path.resolve())}


if __name__ == "__main__":
    cli.run(main)
