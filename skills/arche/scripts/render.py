#!/usr/bin/env python3
"""Render a local HTML report from the scan and import artifacts."""
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("arche: Python 3.9 or newer is required\n")
    sys.exit(2)

import argparse
import re
import time
from pathlib import Path

from arche_lib import cli, gitinfo
from arche_lib.atlas import attach_review, build_atlas, read_descriptions
from arche_lib.audit import build_audit, delta
from arche_lib.audit_jvm import build_jvm_audit
from arche_lib.audit_scripts import build_script_audit
from arche_lib.mapview import EDITORS, render_map
from arche_lib.i18n import LANGUAGES
from arche_lib.errors import IntegrityError, UsageError
from arche_lib.jsonio import check_graph, inventory_digest, read_snapshot, read_json, write_json, write_text
from arche_lib.paths import is_within
from arche_lib.report import render
from arche_lib.runlog import RUN_FILE, record_step
from arche_lib.sources import make_reader
from arche_lib.tour import refresh_tour, validate_tour

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "report-template.html"
MAP_TEMPLATE = TEMPLATE.with_name("map-template.html")


def module_summary(inventory, graph):
    """Count all inventoried paths, including files without an adapter."""
    parsed = {node["id"].removeprefix("file:") for node in graph["nodes"] if node["type"] == "file"}
    modules = {}
    for entry in inventory["files"]:
        path = entry["path"]
        name = path.split("/", 1)[0] if "/" in path else "."
        counts = modules.setdefault(name, {"name": name, "parsed": 0, "excluded": 0})
        counts["parsed" if path in parsed else "excluded"] += 1
    return [modules[name] for name in sorted(modules)]


PROFILE_SECTIONS = ("datasets", "matches", "unresolved", "orphan_datasets", "tables_without_data")


def read_profile(out, graph):
    """profile.json of this snapshot, or None when the run has no data profile."""
    path = out / "profile.json"
    if not path.exists():
        return None
    profile = read_json(path)
    if not isinstance(profile, dict):
        raise IntegrityError("profile.json is not a JSON object; run data_profile.py again")
    if profile.get("profile_version") != 1:
        raise IntegrityError("profile.json has an unsupported profile_version; run data_profile.py again")
    if profile.get("inventory_sha256") != graph["inventory_sha256"]:
        raise IntegrityError("profile.json describes a different snapshot; run data_profile.py again")
    missing = [key for key in PROFILE_SECTIONS if not isinstance(profile.get(key), list)]
    if missing:
        raise IntegrityError(f"profile.json lacks {', '.join(missing)}; run data_profile.py again")
    return profile


COLUMN_SECTIONS = ("columns", "edges", "unresolved", "statements")


def read_columns(out, graph):
    """columns.json of this snapshot, or None when imports.py found no SQL to read."""
    path = out / "columns.json"
    if not path.exists():
        return None
    columns = read_json(path)
    if not isinstance(columns, dict):
        raise IntegrityError("columns.json is not a JSON object; run imports.py again")
    if columns.get("columns_version") != 1:
        raise IntegrityError("columns.json has an unsupported columns_version; run imports.py again")
    if columns.get("inventory_sha256") != graph["inventory_sha256"]:
        raise IntegrityError("columns.json describes a different snapshot; run imports.py again")
    missing = [key for key in COLUMN_SECTIONS if not isinstance(columns.get(key), list)]
    if missing:
        raise IntegrityError(f"columns.json lacks {', '.join(missing)}; run imports.py again")
    return columns


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="directory holding inventory.json and graph.json")
    parser.add_argument("--internal-exports", action="append", default=[], metavar="DIR", help="opt in a repository-relative internal directory for explicit Python export checks")
    parser.add_argument("--base-out", type=Path, help="clean base snapshot run directory for finding comparison")
    parser.add_argument("--tour", type=Path, help="optional tour JSON to validate and include")
    parser.add_argument("--full-details", action="store_true", help="write every file's details into report.html even for large repositories, so it reads without JavaScript")
    parser.add_argument("--editor", choices=sorted(EDITORS), help="add links in map.html that open cited lines in this editor; writes the repository's absolute path into the map")
    parser.add_argument("--lang", choices=LANGUAGES, default="en", help="language of the report interface (default: en)")
    args = parser.parse_args(argv)
    out = args.out
    started = time.time()
    inventory = read_snapshot(out / "inventory.json")
    graph = read_snapshot(out / "graph.json")
    run = read_json(out / RUN_FILE)
    try:
        repo = inventory["repo"]
        root = Path(repo["root"])
        if is_within(root, out):
            raise UsageError("report output must be outside the scanned repository")
        check_graph(inventory, graph)
        commit = repo["commit"]
        if commit is not None and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
            raise IntegrityError("invalid commit in inventory.json")
        commits = gitinfo.recent_commits(root, commit) if commit else None
        before = run["status_before"]
        after = gitinfo.status_hash(root)
        if before is None:
            check = "unavailable"
        else:
            check = "match" if before == after and commit == gitinfo.head_commit(root) else "changed"
        modules = module_summary(inventory, graph)
        tour = read_json(args.tour) if args.tour is not None else None
        moved = []
        if args.tour is not None:
            tour, moved = refresh_tour(tour, inventory, make_reader(inventory))
            validate_tour(tour, inventory, make_reader(inventory))
        changes_path = out / "changes.json"
        changes = read_snapshot(changes_path) if changes_path.exists() else None
        if changes is not None and changes["graph_commit"] != graph["repo"]["commit"]:
            raise IntegrityError("changes.json and graph.json describe different snapshots")
        for prefix in args.internal_exports:
            if not prefix or prefix.startswith('/') or any(part in ('', '.', '..') for part in prefix.split('/')):
                raise UsageError("internal export directories must be relative paths without . or ..")
        internal_exports = sorted(set(args.internal_exports))
        findings = build_audit(graph, make_reader(inventory), internal_exports)
        findings = sorted(findings + build_jvm_audit(graph, inventory, make_reader(inventory))
                          + build_script_audit(graph, make_reader(inventory)),
                          key=lambda finding: (finding["rule"], finding["path"], finding["line"]))
        finding_delta = None
        if args.base_out is not None:
            base_inventory = read_snapshot(args.base_out / "inventory.json")
            base_graph = read_snapshot(args.base_out / "graph.json")
            check_graph(base_inventory, base_graph)
            base_repo = base_inventory["repo"]
            if (changes is None or check != "match" or repo["dirty"] is not False or base_repo["dirty"] is not False
                    or repo["commit"] != changes["head"] or base_repo["commit"] != changes["base"]
                    or base_graph["repo"] != {key: base_repo[key] for key in ("name", "commit", "dirty")}
                    or base_repo["root"] != repo["root"] or base_repo["name"] != repo["name"]):
                raise IntegrityError("finding comparison requires matching clean base/head snapshots and changes.json")
            base_audit = read_json(args.base_out / "audit.json")
            if base_audit.get("repo") != base_graph["repo"] or base_audit.get("schema_version") != 1 or base_audit.get("inventory_sha256") != inventory_digest(base_inventory) or base_audit.get("internal_exports", []) != internal_exports:
                raise IntegrityError("base audit and graph snapshots do not match")
            base_findings = base_audit["findings"]
            finding_delta = delta(base_findings, findings)
        profile = read_profile(out, graph)
        report = render(TEMPLATE.read_text(encoding="utf-8"), graph, commits, check, modules=modules, tour=tour, changes=changes, findings=findings, finding_delta=finding_delta, lang=args.lang, profile=profile, columns=read_columns(out, graph),
                        **({"lazy_file_limit": float("inf")} if args.full_details else {}))
        atlas_path = out / "atlas.json"
        atlas = read_json(atlas_path) if atlas_path.exists() else None
        if atlas is not None and atlas.get("inventory_sha256") != graph["inventory_sha256"]:
            raise IntegrityError("atlas.json describes a different snapshot; run map.py again")
        if atlas is None:
            atlas = build_atlas(inventory, graph, None, read_descriptions(out), changes, findings)
        else:
            atlas = attach_review(atlas, changes, findings)  # the audit runs here, after map.py
        editor = {"name": args.editor, "root": str(root)} if args.editor else None
        atlas_html = render_map(MAP_TEMPLATE.read_text(encoding="utf-8"), atlas, args.lang, editor=editor)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise IntegrityError(f"invalid report inputs: {exc!r}") from None

    path = out / "report.html"
    write_json(out / "audit.json", {"schema_version": 1, "repo": graph["repo"], "inventory_sha256": inventory_digest(inventory), "internal_exports": internal_exports, "findings": findings, "delta": finding_delta})
    write_text(path, report)
    write_text(out / "map.html", atlas_html)
    record_step(out, "render", started, extra={"status_after": after, "readonly_check": check})
    summary = {"step": "render", "report": str(path.resolve()), "map": str((out / "map.html").resolve()), "readonly_check": check}
    if moved:
        summary["tour_steps_reanchored"] = moved
    return summary


if __name__ == "__main__":
    cli.run(main)
