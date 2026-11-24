"""YAML files: paths that known schemas write (Compose, GitHub Actions, Helm, dbt) and SQL file paths.

YAML has no import statement, so the adapter has no imports. Schema paths are
`path_refs` (resolved by graph.py with `pathref`), remote sources are
`config_externals`, and any other value that names a SQL file is a `file_refs`
entry, resolved like the SQL paths written in Python. dbt project settings,
local packages and model names come from `yaml_dbt`.

A file no schema knows (for example a team's own Airflow DAG format) still
links a value that names a source file: such a `path_value` reference is kept
only when that file exists, and is never more than inferred.
"""
from __future__ import annotations

import posixpath
from typing import List

from . import LANG_BY_EXT
from ..fileref import MAX_SPEC_CHARS, SUFFIXES, is_path, is_template
from . import yaml_dbt
from .yaml_reader import scalars
from .yaml_schemas import references, schema_for

MAX_QUOTE_CHARS = 200
# Source files a schema-free value may name; SQL paths are file_refs already.
PATH_VALUE_SUFFIXES = tuple(sorted(ext for ext in LANG_BY_EXT if ext not in SUFFIXES))


def _is_path_value(value: str) -> bool:
    """A single path to a source file: a known suffix, a stem, no whitespace, no URL, no template."""
    lowered = value.lower()
    return (len(value) <= MAX_SPEC_CHARS and lowered.endswith(PATH_VALUE_SUFFIXES)
            and posixpath.splitext(posixpath.basename(lowered))[0] != ""
            and "://" not in value and not any(char.isspace() for char in value)
            and not any(marker in value for marker in ("{{", "${", "$(", "*")))


def _is_chart_template(path: str, text: str) -> bool:
    """A Helm template is Go template text until rendered, not YAML."""
    return "/templates/" in "/" + path and "{{" in text


def extract(path: str, text: str) -> dict:
    if _is_chart_template(path, text):
        return {"imports": [], "symbols": [], "path_refs": [], "config_externals": [], "file_refs": []}
    documents, degraded = scalars(text)
    lines = text.splitlines()
    path_refs, externals, consumed = references(path, documents, lines)
    schema_free = schema_for(path, documents) is None and posixpath.basename(path) != yaml_dbt.PROJECT_FILE
    file_refs: List[dict] = []
    seen = set()
    for document in documents:
        for key, value, line in document:
            if (key, line) in consumed or (line, value) in seen:
                continue
            if schema_free and _is_path_value(value):
                seen.add((line, value))
                path_refs.append({"spec": value, "line": line, "quote": _quote(lines, line),
                                  "via": "path_value:" + _dotted(key), "base": posixpath.dirname(path),
                                  "target": "file", "markers": [], "fallback": True})
                continue
            if not is_path(value):
                continue
            seen.add((line, value))
            file_refs.append({"spec": value, "line": line, "quote": _quote(lines, line), "dynamic": is_template(value)})
    result = {"imports": [], "symbols": [], "path_refs": path_refs, "config_externals": externals,
              "file_refs": sorted(file_refs, key=lambda ref: (ref["line"], ref["spec"])),
              **yaml_dbt.extract(path, documents, lines)}
    return {**result, "degraded": True} if degraded else result


def _quote(lines, line: int) -> str:
    return lines[line - 1].strip()[:MAX_QUOTE_CHARS] if 1 <= line <= len(lines) else ""


def _dotted(key: tuple) -> str:
    """("load", "tasks", 0, "sql_file") -> "load.tasks.[].sql_file"."""
    return ".".join("[]" if isinstance(part, int) else str(part) for part in key)


def build_context(parsed_paths, all_paths, read_path) -> None:
    return None


def resolve(import_record: dict, importer_path: str, context) -> List[dict]:
    return []
