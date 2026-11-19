#!/usr/bin/env python3
"""Export one version 1 tour and its recorded snapshot without rescanning.

Example: python3 export.py --out RUN --tour TOUR.json --format markdown --output tour.md
The JSON export wraps the unmodified tour in an envelope with the inventory repo.
"""
import argparse
import re
from pathlib import Path

from arche_lib import cli
from arche_lib.errors import IntegrityError, UsageError
from arche_lib.jsonio import dumps, read_json, write_text
from arche_lib.paths import is_within


def export_document(inventory, tour):
    """Check the recorded evidence against the inventory, without reading source."""
    if not isinstance(tour, dict) or type(tour.get("version")) is not int or tour["version"] != 1:
        raise IntegrityError("unsupported tour version (expected integer 1)")
    if not isinstance(inventory, dict) or not isinstance(inventory.get("repo"), dict) or not isinstance(inventory.get("files"), list):
        raise IntegrityError("invalid inventory")
    repo = inventory["repo"]
    if any(key not in repo for key in ("name", "commit", "dirty")):
        raise IntegrityError("inventory snapshot is missing metadata")
    if not isinstance(tour.get("steps"), list) or not isinstance(tour.get("edges"), list):
        raise IntegrityError("invalid tour steps or edges")
    files = {}
    for entry in inventory["files"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or entry["path"] in files:
            raise IntegrityError("invalid or duplicate inventory path")
        files[entry["path"]] = entry
    ids = set()
    for step in tour["steps"]:
        if not isinstance(step, dict):
            raise IntegrityError("invalid tour step")
        ident, path, digest = step.get("id"), step.get("path"), step.get("sha256")
        if not isinstance(ident, str) or not ident or ident in ids or not isinstance(path, str) or not path:
            raise IntegrityError("invalid or duplicate tour step ID or path")
        ids.add(ident)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise IntegrityError("invalid tour hash")
        entry = files.get(path)
        if entry is None or entry.get("status") == "skipped" or entry.get("sha256") != digest:
            raise IntegrityError("tour evidence does not match inventory")
        start, end = step.get("start_line"), step.get("end_line")
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            raise IntegrityError("invalid tour line range")
    for edge in tour["edges"]:
        if (not isinstance(edge, dict) or not all(isinstance(edge.get(k), str) for k in ("from", "to", "kind"))
                or edge["from"] not in ids or edge["to"] not in ids
                or edge["kind"] not in {"calls", "reads", "writes", "artifact_transfer"}):
            raise IntegrityError("invalid tour edge")
    return {"version": 1, "repo": {k: repo[k] for k in ("name", "commit", "dirty")}, "tour": tour}


def _text(value):
    # Entity encoding also prevents Markdown punctuation and HTML injection.
    return "".join(c if c.isalnum() or c in " ./_-" else f"&#{ord(c)};" for c in str(value))


def render_export(document, fmt):
    if fmt == "json":
        return dumps(document)
    repo, tour = document["repo"], document["tour"]
    if fmt == "markdown":
        lines = ["# Tour", "", f"Repository: {_text(repo['name'])}  ", f"Revision: {_text(repo['commit'])}  ", f"Dirty: {_text(repo['dirty'])}  ", f"Tour version: {tour['version']}", "", "## Steps", ""]
        for step in tour["steps"]:
            lines += [f"### {_text(step['id'])}", "", f"Path: {_text(step['path'])}  ", f"Lines: {step['start_line']}–{step['end_line']}  ", f"SHA256: {step['sha256']}", ""]
        lines += ["## Edges", ""]
        lines += [f"- {_text(e['from'])} → {_text(e['to'])} ({_text(e['kind'])})" for e in tour["edges"]]
        return "\n".join(lines) + "\n"
    if fmt == "mermaid":
        lines = ["flowchart TD", f"  %% Repository: {_text(repo['name'])}; revision: {_text(repo['commit'])}; dirty: {_text(repo['dirty'])}; tour version: {tour['version']}"]
        nodes = {step["id"]: f"n{i}" for i, step in enumerate(tour["steps"])}
        for step in tour["steps"]:
            label = f"{step['id']} | {step['path']}:{step['start_line']}-{step['end_line']} | SHA256 {step['sha256']}"
            lines.append(f'  {nodes[step["id"]]}["{_text(label)}"]')
        for edge in tour["edges"]:
            lines.append(f'  {nodes[edge["from"]]} -->|"{_text(edge["kind"])}"| {nodes[edge["to"]]}')
        return "\n".join(lines) + "\n"
    raise ValueError("unknown format")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="existing run directory")
    parser.add_argument("--tour", required=True, type=Path, help="tour JSON")
    parser.add_argument("--format", required=True, choices=("json", "markdown", "mermaid"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    inventory = read_json(args.out / "inventory.json")
    document = export_document(inventory, read_json(args.tour))
    root = inventory["repo"].get("root")
    if root and is_within(Path(root), args.output):
        raise UsageError("export output must be outside the scanned repository")
    if args.output.resolve() in {args.tour.resolve(), (args.out / "inventory.json").resolve()}:
        raise UsageError("export output must not overwrite an input")
    write_text(args.output, render_export(document, args.format))
    return {"step": "export", "format": args.format, "export": str(args.output.resolve())}


if __name__ == "__main__":
    cli.run(main)
