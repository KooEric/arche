"""What dbt YAML files tell the graph step: project settings, local packages and model names.

`dbt_project.yml` gives the project name and where models, snapshots and seeds live.
`packages.yml` lists local packages. A property file (`models: - name: orders`)
names models that graph.py links to the model file of the same name.
"""
from __future__ import annotations

import posixpath
from typing import Dict, List, Optional, Sequence

from .yaml_reader import Scalar
from .yaml_schemas import DBT_PACKAGE_FILES

PROJECT_FILE = "dbt_project.yml"
PATH_KEYS = {"model-paths": ["models"], "snapshot-paths": ["snapshots"], "seed-paths": ["seeds"]}
# dbt before 1.0 spelled these source-paths and data-paths.
LEGACY_KEYS = {"source-paths": "model-paths", "data-paths": "seed-paths"}
NAMED_KINDS = ("models", "snapshots", "seeds")
MAX_QUOTE_CHARS = 200


def project(documents: Sequence[Sequence[Scalar]]) -> Optional[dict]:
    """{"name", "model-paths", "snapshot-paths", "seed-paths"} from dbt_project.yml."""
    found: Dict[str, List[str]] = {}
    name = None
    for document in documents:
        for key, value, _ in document:
            if key == ("name",):
                name = value
            elif len(key) == 2 and isinstance(key[1], int):
                setting = LEGACY_KEYS.get(key[0], key[0])
                if setting in PATH_KEYS:
                    found.setdefault(setting, []).append(value.strip("/"))
    return {"name": name, **{setting: found.get(setting, default) for setting, default in PATH_KEYS.items()}}


def local_packages(path: str, documents: Sequence[Sequence[Scalar]]) -> List[str]:
    """Repository-relative directories of `packages: - local: DIR` entries."""
    base = posixpath.dirname(path)
    dirs = []
    for document in documents:
        for key, value, _ in document:
            if len(key) == 3 and key[0] == "packages" and key[2] == "local":
                joined = posixpath.normpath(posixpath.join(base, value))
                if joined != ".." and not joined.startswith("../"):
                    dirs.append("" if joined == "." else joined)
    return dirs


def names(documents: Sequence[Sequence[Scalar]], lines: Sequence[str]) -> List[dict]:
    """[{"name", "kind", "line", "quote"}] for `models|snapshots|seeds: - name: X`."""
    found = []
    for document in documents:
        for key, value, line in document:
            if len(key) == 3 and key[0] in NAMED_KINDS and isinstance(key[1], int) and key[2] == "name":
                quote = lines[line - 1].strip()[:MAX_QUOTE_CHARS] if 1 <= line <= len(lines) else ""
                found.append({"name": value, "kind": key[0], "line": line, "quote": quote})
    return found


def extract(path: str, documents: Sequence[Sequence[Scalar]], lines: Sequence[str]) -> dict:
    """The dbt keys of one YAML file's extract result; empty for other files."""
    name = posixpath.basename(path)
    result: dict = {}
    if name == PROJECT_FILE:
        result["dbt_project"] = project(documents)
    elif name in DBT_PACKAGE_FILES:
        result["dbt_local_packages"] = local_packages(path, documents)
    else:
        found = names(documents, lines)
        if found:
            result["dbt_names"] = found
    return result
