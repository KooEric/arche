"""Why YAML files are not linked: group them by key structure and test the linking rules.

For each group of YAML files with the same top-level keys the report gives the file
count, the folders they sit in, the shape of each key's values (never the values),
and three signals that decide which linking rule fits:

  path_value       a value names a file that exists in the repository
  python_literal   some Python file writes this file's name as a string literal
  same_folder_py   a Python file in the same folder loads YAML

Files a known schema reads (Compose, GitHub Actions, Helm, dbt) and Helm templates
are left out: arche links those already.
"""
from __future__ import annotations

import posixpath
import re
from collections import Counter, defaultdict
from typing import Callable, Dict, Iterable, Optional, Set

from .langs import yaml_dbt
from .langs.yaml_config import _is_chart_template
from .langs.yaml_reader import scalars
from .langs.yaml_schemas import schema_for

TOP_FOLDERS = 5
TOP_SHAPES = 3
MAJORITY = 0.5
_YAML_LITERAL = re.compile(r"""["']([^"'\s{}]*\.ya?ml)["']""", re.I)  # `{}` would be an f-string field
_LOADS_YAML = re.compile(r"\byaml\.\w+\s*\(|\w*yaml\w*\s*\(", re.I)
_DOTTED = re.compile(r"^[A-Za-z_]\w*(\.\w+)+$")
_WORD = re.compile(r"^\w+$")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_FILE_LIKE = re.compile(r"^[^\s]+\.[A-Za-z0-9]{1,6}$")

COVERED = "covered"
RULES = (
    ("path_value", "a value names a repository file: arche links it already (path_value)"),
    ("python_literal", "some Python file writes this file name: arche links it when the name resolves next to "
                       "that Python file, at the repository root or by a unique path (python:yaml_path)"),
    ("same_folder_py", "a Python file in the same folder loads YAML: arche links it (python:same_folder_yaml)"),
)
NO_RULE = "no static link found: the path is built at run time or the file is read elsewhere"


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


def diagnose(paths: Iterable[str], read_path: Callable[[str], Optional[str]],
             linked: Optional[Set[str]] = None) -> dict:
    """`paths` are the scanned repository paths; `linked` the YAML files that already have an edge."""
    paths = sorted(paths)
    path_set = set(paths)
    named: Set[str] = set()
    loads_yaml_dirs: Set[str] = set()
    for path in paths:
        if path.endswith(".py"):
            text = read_path(path) or ""
            named.update(posixpath.basename(match.group(1)) for match in _YAML_LITERAL.finditer(text))
            if _LOADS_YAML.search(text):
                loads_yaml_dirs.add(posixpath.dirname(path))

    groups: Dict[tuple, dict] = {}
    skipped = Counter()
    for path in paths:
        if not path.lower().endswith((".yaml", ".yml")):
            continue
        text = read_path(path)
        if text is None:
            skipped["unreadable"] += 1
            continue
        if _is_chart_template(path, text):
            skipped["helm_template"] += 1
            continue
        documents, _ = scalars(text)
        name = posixpath.basename(path)
        if schema_for(path, documents) is not None or name in (yaml_dbt.PROJECT_FILE,) or yaml_dbt.names(documents, []):
            skipped["known_schema"] += 1
            continue
        directory = posixpath.dirname(path)

        def exists(value: str, directory=directory) -> bool:
            clean = value.lstrip("./") if value.startswith("./") else value
            return (posixpath.normpath(posixpath.join(directory, clean)) in path_set
                    or posixpath.normpath(clean) in path_set)

        top = tuple(sorted({str(key[0]) for document in documents for key, _, _ in document if key}))
        group = groups.setdefault(top, {"files": 0, "linked": 0, "folders": Counter(), "shapes": defaultdict(Counter),
                                        "path_value": 0, "python_literal": 0, "same_folder_py": 0, COVERED: 0})
        group["files"] += 1
        group["linked"] += bool(linked and path in linked)
        group["folders"]["/".join(directory.split("/")[:2]) or "."] += 1
        has_path_value = False
        for document in documents:
            for key, value, _ in document:
                kind = shape(value, exists)
                group["shapes"][_key_name(key, 2)][kind] += 1
                has_path_value = has_path_value or kind == "existing_file"
        signals = (has_path_value, name in named, directory in loads_yaml_dirs)
        group["path_value"] += signals[0]
        group["python_literal"] += signals[1]
        group["same_folder_py"] += signals[2]
        group[COVERED] += any(signals)
    return {"groups": [_summary(top, group, linked is not None) for top, group in
                       sorted(groups.items(), key=lambda item: (-item[1]["files"], item[0]))],
            "skipped": dict(sorted(skipped.items()))}


def _summary(top: tuple, group: dict, with_linked: bool) -> dict:
    files = group["files"]
    signals = {name: group[name] for name, _ in RULES}
    rules = [text for name, text in sorted(RULES, key=lambda rule: -signals[rule[0]]) if signals[name]]
    if not rules or group[COVERED] < MAJORITY * files:
        rules.append(NO_RULE)
    return {
        "top_keys": list(top),
        "files": files,
        **({"linked": group["linked"]} if with_linked else {}),
        "folders": dict(group["folders"].most_common(TOP_FOLDERS)),
        "keys": {key: dict(counts.most_common(TOP_SHAPES)) for key, counts in sorted(group["shapes"].items())},
        "signals": signals,
        "uncovered": files - group[COVERED],
        "recommendation": rules,
    }


def render_text(result: dict) -> str:
    lines = ["YAML LINK DIAGNOSIS (values are never printed; only key names, value shapes and counts)", ""]
    for number, group in enumerate(result["groups"], 1):
        linked = f", {group['linked']} already linked" if "linked" in group else ""
        lines.append(f"GROUP {number}: {group['files']} files{linked}")
        lines.append("  top keys: " + (", ".join(group["top_keys"]) or "(none)"))
        lines.append("  folders: " + ", ".join(f"{folder}/ {count}" for folder, count in group["folders"].items()))
        signals = group["signals"]
        lines.append(f"  value names a repository file: {signals['path_value']}   "
                     f"Python writes the file name: {signals['python_literal']}   "
                     f"same-folder Python loads YAML: {signals['same_folder_py']}")
        lines.append(f"  files no rule covers: {group['uncovered']}")
        lines += ["  rule: " + rule for rule in group["recommendation"]]
        lines.append("  key shapes:")
        lines += [f"    {key}: " + ", ".join(f"{kind} {count}" for kind, count in shapes.items())
                  for key, shapes in group["keys"].items()]
        lines.append("")
    skipped = result["skipped"]
    lines.append("LEFT OUT: " + (", ".join(f"{reason} {count}" for reason, count in skipped.items()) or "none"))
    return "\n".join(lines) + "\n"
