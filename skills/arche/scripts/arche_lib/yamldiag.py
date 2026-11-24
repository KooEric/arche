"""Why YAML files are not linked: group them by key structure and check what arche links.

For each group of YAML files with the same top-level keys the report gives the file
count, the folders they sit in and the shape of each key's values (never the values).
It runs arche's own extraction and path resolution, so its numbers are what arche links:

  linked_by_value   a value in the file names a repository file (path_value)
  linked_by_python  Python reads the file: by name, pattern, convention or same folder
                    (python:yaml_path, python:yaml_name, python:yaml_glob,
                    python:same_folder_yaml)
  pattern_only      a shared loader builds this file's name, but the file has no Python
                    beside it, so it is covered for change impact without an edge
  name_unresolved   Python writes the file's name, but arche cannot resolve it to this
                    file; the folders of that Python are listed as leads

Files a known schema reads (Compose, GitHub Actions, Helm, dbt) and Helm templates
are left out: arche links those already. Only the files the scan parses are read.
"""
from __future__ import annotations

import posixpath
import re
from collections import Counter, defaultdict
from typing import Callable, Dict, Iterable, Optional, Set

from . import pathref
from .langs import yaml_config, yaml_dbt
from .langs.python_yamlrefs import yaml_refs
from .langs.yaml_reader import scalars
from .langs.yaml_schemas import schema_for

TOP_FOLDERS = 5
TOP_SHAPES = 3
TOP_LEADS = 3
_DOTTED = re.compile(r"^[A-Za-z_]\w*(\.\w+)+$")
_WORD = re.compile(r"^\w+$")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_FILE_LIKE = re.compile(r"^[^\s]+\.[A-Za-z0-9]{1,6}$")


def shape(value: str, exists: Callable[[str], bool]) -> str:
    """A value's shape, never the value: existing_file, path, template, url, number, bool,
    dotted_name, word or text."""
    lowered = value.lower()
    if "{{" in value or "${" in value:
        return "template"
    if "://" in value:
        return "url"
    if lowered in ("true", "false", "yes", "no", "on", "off"):
        return "bool"
    if _NUMBER.match(value):
        return "number"
    if _FILE_LIKE.match(value) and exists(value):
        return "existing_file"
    if "/" in value and " " not in value:
        return "path"
    if _DOTTED.match(value):
        return "dotted_name"
    if _WORD.match(value):
        return "word"
    return "text"


def _key_name(key: tuple, depth: int) -> str:
    return ".".join("[]" if isinstance(part, int) else str(part) for part in key[:depth])


def _has_python_sibling(path: str, loader: str, index: pathref.Index) -> bool:
    return any(sibling.endswith(".py") and sibling in index.analyzed and sibling != loader
               for sibling in index.children.get(posixpath.dirname(path), []))


def _python_links(python: Iterable[str], read_path, index: pathref.Index):
    """(YAML path -> vias of the edges arche draws to it, YAML paths a loader pattern covers
    without an edge of their own, YAML name -> folders of Python that write it unresolved).

    Mirrors graph.py: a name several files carry gets one edge from the loader to the
    first of them, and each of them an edge from the Python in its own folder."""
    links: Dict[str, Set[str]] = defaultdict(set)
    pattern_only: Set[str] = set()
    unresolved: Dict[str, Set[str]] = defaultdict(set)
    for path in python:
        text = read_path(path)
        if not text:
            continue
        for ref in yaml_refs(path, text):
            results = [r for r in pathref.resolve_all(ref, index) if r["status"] in ("verified", "inferred")]
            many = ref["target"] == "name" and len(results) > 1
            for position, result in enumerate(results):
                if not many or position == 0:
                    links[result["to"]].add(ref["via"])
                elif _has_python_sibling(result["to"], path, index):
                    links[result["to"]].add(ref["via"] + " (same folder)")
                else:
                    pattern_only.add(result["to"])
            if not results and not ref.get("dynamic") and ref["target"] in ("file", "name"):
                unresolved[posixpath.basename(ref["spec"])].add(posixpath.dirname(path) or ".")
    return links, pattern_only - set(links), unresolved


def diagnose(parsed: Iterable[str], all_paths: Iterable[str], read_path: Callable[[str], Optional[str]],
             linked: Optional[Set[str]] = None) -> dict:
    """`parsed` are the files the scan parses, `all_paths` every scanned path; `linked` the
    YAML files that already have an edge in a run."""
    parsed = sorted(parsed)
    index = pathref.build_index(all_paths, parsed)
    links, pattern_only, unresolved = _python_links((p for p in parsed if p.endswith(".py")), read_path, index)

    groups: Dict[tuple, dict] = {}
    skipped = Counter()
    for path in parsed:
        if not path.lower().endswith((".yaml", ".yml")):
            continue
        text = read_path(path)
        if text is None:
            skipped["unreadable"] += 1
            continue
        if yaml_config._is_chart_template(path, text):
            skipped["helm_template"] += 1
            continue
        documents, _ = scalars(text)
        name = posixpath.basename(path)
        if schema_for(path, documents) is not None or name == yaml_dbt.PROJECT_FILE or yaml_dbt.names(documents, []):
            skipped["known_schema"] += 1
            continue
        directory = posixpath.dirname(path)

        def exists(value: str, directory=directory) -> bool:
            clean = value[2:] if value.startswith("./") else value
            return (posixpath.normpath(posixpath.join(directory, clean)) in index.paths
                    or posixpath.normpath(clean) in index.paths)

        by_value = any(pathref.resolve(ref, index) for ref in yaml_config.extract(path, text)["path_refs"])
        by_python = links.get(path, set())
        covered_by_pattern = path in pattern_only
        leads = unresolved.get(name, set()) if not by_python and not covered_by_pattern else set()

        top = tuple(sorted({str(key[0]) for document in documents for key, _, _ in document if key}))
        group = groups.setdefault(top, {"files": 0, "linked": 0, "folders": Counter(), "shapes": defaultdict(Counter),
                                        "linked_by_value": 0, "linked_by_python": 0, "vias": Counter(),
                                        "name_unresolved": 0, "leads": Counter(), "covered": 0, "pattern_only": 0})
        group["files"] += 1
        group["linked"] += bool(linked and path in linked)
        group["folders"]["/".join(directory.split("/")[:2]) or "."] += 1
        for document in documents:
            for key, value, _ in document:
                group["shapes"][_key_name(key, 2)][shape(value, exists)] += 1
        group["linked_by_value"] += by_value
        group["linked_by_python"] += bool(by_python)
        group["vias"].update(by_python)
        group["name_unresolved"] += bool(leads) and not by_value
        group["leads"].update(leads)
        group["pattern_only"] += covered_by_pattern and not by_python and not by_value
        group["covered"] += bool(by_value or by_python or leads or covered_by_pattern)
    return {"groups": [_summary(top, group, linked is not None) for top, group in
                       sorted(groups.items(), key=lambda item: (-item[1]["files"], item[0]))],
            "skipped": dict(sorted(skipped.items()))}


def _summary(top: tuple, group: dict, with_linked: bool) -> dict:
    return {
        "top_keys": list(top),
        "files": group["files"],
        **({"linked": group["linked"]} if with_linked else {}),
        "folders": dict(group["folders"].most_common(TOP_FOLDERS)),
        "keys": {key: dict(counts.most_common(TOP_SHAPES)) for key, counts in sorted(group["shapes"].items())},
        "linked_by_value": group["linked_by_value"],
        "linked_by_python": group["linked_by_python"],
        "python_rules": dict(sorted(group["vias"].items())),
        "pattern_only": group["pattern_only"],
        "name_unresolved": group["name_unresolved"],
        "name_written_in": sorted(group["leads"], key=lambda folder: (-group["leads"][folder], folder))[:TOP_LEADS],
        "no_evidence": group["files"] - group["covered"],
    }


def render_text(result: dict) -> str:
    lines = ["YAML LINK DIAGNOSIS (values are never printed; only key names, value shapes and counts)",
             "Counts come from arche's own extraction and path resolution, so they are what arche links.", ""]
    for number, group in enumerate(result["groups"], 1):
        linked = f", {group['linked']} linked in the given run" if "linked" in group else ""
        lines.append(f"GROUP {number}: {group['files']} files{linked}")
        lines.append("  top keys: " + (", ".join(group["top_keys"]) or "(none)"))
        lines.append("  folders: " + ", ".join(f"{folder}/ {count}" for folder, count in group["folders"].items()))
        rules = ", ".join(f"{via} {count}" for via, count in group["python_rules"].items())
        lines.append(f"  arche links from Python: {group['linked_by_python']}" + (f" ({rules})" if rules else ""))
        lines.append(f"  arche links by a value naming a file: {group['linked_by_value']}")
        if group["pattern_only"]:
            lines.append(f"  read by a loader pattern, no edge of its own (no Python in its folder): {group['pattern_only']}")
        if group["name_unresolved"]:
            lines.append(f"  name written in Python but not resolvable to this file: {group['name_unresolved']}"
                         f" (Python in: {', '.join(group['name_written_in'])}; check how that code builds the folder)")
        lines.append(f"  no static evidence: {group['no_evidence']}")
        lines.append("  key shapes:")
        lines += [f"    {key}: " + ", ".join(f"{kind} {count}" for kind, count in shapes.items())
                  for key, shapes in group["keys"].items()]
        lines.append("")
    skipped = result["skipped"]
    lines.append("LEFT OUT: " + (", ".join(f"{reason} {count}" for reason, count in skipped.items()) or "none"))
    return "\n".join(lines) + "\n"
