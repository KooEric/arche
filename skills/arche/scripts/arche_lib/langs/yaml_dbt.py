"""What dbt YAML files tell the graph step: project settings, local packages, model names and docs.

`dbt_project.yml` gives the project name, where models, snapshots and seeds live,
and the `+alias`/`+schema` set for model folders. `packages.yml` lists local
packages. A property file (`models: - name: orders`) names models that graph.py
links to the model file of the same name; it is read again with block scalars on
for descriptions, columns, tests and sources.
"""
from __future__ import annotations

import posixpath
import re
from typing import Dict, List, Optional, Sequence, Tuple

from .yaml_reader import Scalar, scalars
from .yaml_schemas import DBT_PACKAGE_FILES

PROJECT_FILE = "dbt_project.yml"
PATH_KEYS = {"model-paths": ["models"], "snapshot-paths": ["snapshots"], "seed-paths": ["seeds"]}
# dbt before 1.0 spelled these source-paths and data-paths.
LEGACY_KEYS = {"source-paths": "model-paths", "data-paths": "seed-paths"}
NAMED_KINDS = ("models", "snapshots", "seeds")
MAX_QUOTE_CHARS = 200
FOLDER_KINDS = ("models", "snapshots")
FOLDER_SETTINGS = {"+alias": "alias", "alias": "alias", "+schema": "schema", "schema": "schema"}
MAX_DESCRIPTION_CHARS = 2_000
MAX_VALUE_CHARS = 200
MAX_TEST_VALUES = 10
TEST_KEYS = ("tests", "data_tests")
_DOC_ONLY = re.compile(r"\{\{-?\s*doc\(\s*(?:'([^'\n]{1,200})'|\"([^\"\n]{1,200})\")\s*\)\s*-?\}\}")
_QUOTED = r"\s*(?:'([^'\n]*)'|\"([^\"\n]*)\")\s*"
_REF_TO = re.compile(rf"(?:\{{\{{\s*)?ref\({_QUOTED}\)(?:\s*\}}\}})?")
_SOURCE_TO = re.compile(rf"(?:\{{\{{\s*)?source\({_QUOTED},{_QUOTED}\)(?:\s*\}}\}})?")


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


def folder_config(documents: Sequence[Sequence[Scalar]], name: Optional[str]) -> List[dict]:
    """[{"kind", "folders", "setting", "value", "line"}] for `models: <name>: <folder>...: +alias|+schema: X`.

    `alias:` and `schema:` without `+` (the older spelling) mean the same."""
    found = []
    for document in documents:
        for key, value, line in document:
            if (name and len(key) >= 3 and key[0] in FOLDER_KINDS and key[1] == name and key[-1] in FOLDER_SETTINGS
                    and all(isinstance(part, str) for part in key)
                    and not any(part.startswith("+") for part in key[2:-1])):
                found.append({"kind": key[0], "folders": list(key[2:-1]), "setting": FOLDER_SETTINGS[key[-1]],
                              "value": _cap(value), "line": line})
    return found


Node = Dict[object, object]


def _tree(document: Sequence[Scalar]) -> Node:
    """Nested dicts by key path; a leaf is (value, line). The first value at a path wins."""
    root: Node = {}
    for path, value, line in document:
        node = root
        for part in path[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                break
            node = child
        else:
            node.setdefault(path[-1], (value, line))
    return root


def _items(node: object) -> List[Node]:
    """The mapping items of a block sequence node, in order."""
    if not isinstance(node, dict):
        return []
    return [node[index] for index in sorted(key for key in node if isinstance(key, int)) if isinstance(node[index], dict)]


def _cap(value: Optional[str]) -> Optional[str]:
    """A value from the file, cut to MAX_VALUE_CHARS; untrusted input is never kept whole."""
    return value[:MAX_VALUE_CHARS] if value is not None else None


def _leaf(node: Node, key: str) -> Optional[str]:
    found = node.get(key)
    return found[0] if isinstance(found, tuple) else None


def _first_line(node: object) -> int:
    """The smallest line under a node, found without recursion so deep nesting cannot overflow."""
    lines, pending = [], [node]
    while pending:
        current = pending.pop()
        if isinstance(current, tuple):
            lines.append(current[1])
        elif isinstance(current, dict):
            pending.extend(current.values())
    return min(lines) if lines else 0


def _description(node: Node, cut_lines: set) -> dict:
    """{"description", "doc"} plus `description_truncated` when it was cut."""
    found = node.get("description")
    if not isinstance(found, tuple):
        return {"description": None, "doc": None}
    text, line = found
    doc = _DOC_ONLY.fullmatch(text.strip())
    if doc:
        return {"description": None, "doc": doc.group(1) or doc.group(2)}
    cut = line in cut_lines or len(text) > MAX_DESCRIPTION_CHARS
    return {"description": text[:MAX_DESCRIPTION_CHARS], "doc": None, **({"description_truncated": True} if cut else {})}


def _relation(text: str) -> dict:
    ref = _REF_TO.fullmatch(text.strip())
    if ref:
        return {"ref": _cap(ref.group(1) if ref.group(1) is not None else ref.group(2))}
    source = _SOURCE_TO.fullmatch(text.strip())
    if source:
        return {"source": [_cap(source.group(1) if source.group(1) is not None else source.group(2)),
                           _cap(source.group(3) if source.group(3) is not None else source.group(4))]}
    return {"dynamic": True}


def _test(item: object) -> Optional[Tuple[int, dict]]:
    """(line, test) for `- not_null` or `- accepted_values: {values: [...]}`; arguments may sit under `arguments:`."""
    if isinstance(item, tuple):
        return item[1], {"name": item[0][:MAX_VALUE_CHARS]}
    names = [key for key in item if isinstance(key, str)] if isinstance(item, dict) else []
    if not names:
        return None
    name = names[0]
    args = item[name] if isinstance(item[name], dict) else {}
    if isinstance(args.get("arguments"), dict):
        args = {**args, **args["arguments"]}
    test: dict = {"name": name[:MAX_VALUE_CHARS]}
    if name == "accepted_values":
        listed = args.get("values") if isinstance(args.get("values"), dict) else {}
        values = [listed[key][0][:MAX_VALUE_CHARS] for key in sorted(k for k in listed if isinstance(k, int))
                  if isinstance(listed[key], tuple)]
        test.update(values=values[:MAX_TEST_VALUES], values_count=len(values))
    elif name == "relationships":
        to, field = _leaf(args, "to"), _leaf(args, "field")
        test.update({**({"to": _relation(to)} if to is not None else {}),
                     **({"field": field[:MAX_VALUE_CHARS]} if field is not None else {})})
    return _first_line(item), test


def _tests(node: Node) -> List[dict]:
    """Tests under `tests:` and `data_tests:`, in line order."""
    found = []
    for key in TEST_KEYS:
        sequence = node.get(key)
        if not isinstance(sequence, dict):
            continue
        for index in sorted(k for k in sequence if isinstance(k, int)):
            test = _test(sequence[index])
            if test:
                found.append(test)
    return [test for _, test in sorted(found, key=lambda pair: pair[0])]


def _columns(node: Node, cut_lines: set) -> List[dict]:
    columns = []
    for column in _items(node.get("columns")):
        name = _leaf(column, "name")
        if name is None:
            continue
        described = _description(column, cut_lines)
        data_type = _leaf(column, "data_type")
        columns.append({"name": _cap(name), "description": described["description"],
                        **({"description_truncated": True} if "description_truncated" in described else {}),
                        "data_type": data_type[:MAX_VALUE_CHARS] if data_type else None, "tests": _tests(column)})
    return columns


def _named(node: Node, cut_lines: set) -> Optional[dict]:
    """{"name", "line", "description", "doc", "columns"} of a model, snapshot, seed or source table."""
    found = node.get("name")
    if not isinstance(found, tuple):
        return None
    return {"name": _cap(found[0]), "line": found[1], **_description(node, cut_lines), "columns": _columns(node, cut_lines)}


def properties(documents: Sequence[Sequence[Scalar]], cut: Sequence[Tuple[tuple, int]] = ()) -> dict:
    """{"models": [...], "sources": [...]} documented in a property file; `cut` lists block scalars cut short."""
    cut_lines = {line for _, line in cut}
    models, sources = [], []
    for document in documents:
        tree = _tree(document)
        for kind in NAMED_KINDS:
            for item in _items(tree.get(kind)):
                entry = _named(item, cut_lines)
                if entry:
                    models.append({"kind": kind, **entry, "tests": _tests(item)})
        for item in _items(tree.get("sources")):
            entry = _named(item, cut_lines)
            if entry is None:
                continue
            tables = []
            for node in _items(item.get("tables")):
                table = _named(node, cut_lines)
                if table:
                    tables.append({**table, "identifier": _cap(_leaf(node, "identifier"))})
            flagged = {"description_truncated": True} if entry.get("description_truncated") else {}
            sources.append({"name": entry["name"], "line": entry["line"], "schema": _cap(_leaf(item, "schema")),
                            "database": _cap(_leaf(item, "database")), "description": entry["description"], **flagged,
                            "tables": tables})
    return {"models": models, "sources": sources}


def extract(path: str, documents: Sequence[Sequence[Scalar]], lines: Sequence[str], text: str = "") -> dict:
    """The dbt keys of one YAML file's extract result; empty for other files."""
    name = posixpath.basename(path)
    result: dict = {}
    if name == PROJECT_FILE:
        result["dbt_project"] = project(documents)
        settings = folder_config(documents, result["dbt_project"]["name"])
        if settings:
            result["dbt_folder_config"] = settings
    elif name in DBT_PACKAGE_FILES:
        result["dbt_local_packages"] = local_packages(path, documents)
    else:
        found = names(documents, lines)
        if found:
            result["dbt_names"] = found
        if found or any(key[:1] == ("sources",) and key[-1] == "name" for document in documents for key, _, _ in document):
            cut: List[Tuple[tuple, int]] = []
            full, _ = scalars(text, block_scalars=True, truncated=cut)
            result["dbt_props"] = properties(full, cut)
    return result
