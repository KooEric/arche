"""YAML files: paths that known schemas write (Compose, GitHub Actions, Helm) and SQL file paths.

YAML has no import statement, so the adapter has no imports. Schema paths are
`path_refs` (resolved by graph.py with `pathref`), remote sources are
`config_externals`, and any other value that names a SQL file is a `file_refs`
entry, resolved like the SQL paths written in Python.
"""
from __future__ import annotations

from typing import List

from ..fileref import is_path, is_template
from .yaml_reader import scalars
from .yaml_schemas import references

MAX_QUOTE_CHARS = 200


def _is_chart_template(path: str, text: str) -> bool:
    """A Helm template is Go template text until rendered, not YAML."""
    return "/templates/" in "/" + path and "{{" in text


def extract(path: str, text: str) -> dict:
    if _is_chart_template(path, text):
        return {"imports": [], "symbols": [], "path_refs": [], "config_externals": [], "file_refs": []}
    documents, degraded = scalars(text)
    lines = text.splitlines()
    path_refs, externals, consumed = references(path, documents, lines)
    file_refs: List[dict] = []
    seen = set()
    for document in documents:
        for key, value, line in document:
            if (key, line) in consumed or not is_path(value) or (line, value) in seen:
                continue
            seen.add((line, value))
            quote = lines[line - 1].strip()[:MAX_QUOTE_CHARS] if 1 <= line <= len(lines) else ""
            file_refs.append({"spec": value, "line": line, "quote": quote, "dynamic": is_template(value)})
    result = {"imports": [], "symbols": [], "path_refs": path_refs, "config_externals": externals,
              "file_refs": sorted(file_refs, key=lambda ref: (ref["line"], ref["spec"]))}
    return {**result, "degraded": True} if degraded else result


def build_context(parsed_paths, all_paths, read_path) -> None:
    return None


def resolve(import_record: dict, importer_path: str, context) -> List[dict]:
    return []
