#!/usr/bin/env python3
"""Select a feature by a screen label, behavior, step ID or source path."""
import argparse
from pathlib import Path

from arche_lib import cli
from arche_lib.errors import UsageError
from arche_lib.focus import http_steps, mermaid, select
from arche_lib.jsonio import check_graph, read_json, read_snapshot, write_json, write_text
from arche_lib.paths import is_within
from arche_lib.sources import make_reader
from arche_lib.tour import validate_tour



def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--tour", required=True, type=Path)
    parser.add_argument("--query", required=True)
    parser.add_argument("--hops", type=int, choices=range(0, 9), default=2)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--format", choices=("json", "mermaid"), default="json")
    args = parser.parse_args(argv)
    inventory = read_snapshot(args.out / "inventory.json")
    graph = read_snapshot(args.out / "graph.json")
    check_graph(inventory, graph)
    if is_within(Path(inventory["repo"]["root"]), args.output) or args.output.exists():
        raise UsageError("choose a new output file outside the repository")
    tour = read_json(args.tour)
    validate_tour(tour, inventory, make_reader(inventory))
    focused = select(tour, args.query, args.hops, graph.get("http_connections", []))
    if args.format == "mermaid":
        write_text(args.output, mermaid(focused))
    else:
        write_json(args.output, focused)
    return {"tour": str(args.output.resolve()), "steps": len(focused["steps"]), "edges": len(focused["edges"])}


if __name__ == "__main__":
    cli.run(main)
