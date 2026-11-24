"""YAML files a Python file reads: literal paths, patterns, and the same-folder convention.

Teams often keep a DAG's configuration next to its code and load it with
`yaml.safe_load(open(DAG_DIR / "dag.yaml"))` or a helper such as `load_yaml_file`.
The YAML holds parameters, not paths, so the link lives in the Python file:

  - a string literal naming a .yaml/.yml file (`"dag.yaml"`, `"conf/a.yml"`) is looked up
    next to the Python file, then from the repository root, then by a unique path suffix
    (`python:yaml_path`, inferred; a miss leaves nothing behind);
  - a literal pattern (`"configs/*.yaml"`) matches files under the Python file's folder
    (`python:yaml_glob`, inferred);
  - a file that loads YAML (a call whose name holds "yaml", or on the `yaml` module) but
    names no YAML file links to the YAML files in its own folder
    (`python:same_folder_yaml`, inferred, kept only when the folder has one);
  - an f-string naming a YAML file is built at run time: it is recorded as dynamic and
    not followed.
"""
from __future__ import annotations

import ast
import posixpath
from typing import List, Optional, Sequence, Set

SUFFIXES = (".yaml", ".yml")
MAX_SPEC_CHARS = 200
MAX_QUOTE_CHARS = 200
_GLOB_CHARS = "*?["


def _quote(lines: Sequence[str], line: int) -> str:
    return lines[line - 1].strip()[:MAX_QUOTE_CHARS] if 1 <= line <= len(lines) else ""


def _names_yaml_file(text: str) -> bool:
    """A single YAML path or pattern: a YAML suffix, a stem (or a pattern), no whitespace, no URL."""
    return (len(text) <= MAX_SPEC_CHARS and text.lower().endswith(SUFFIXES) and "://" not in text
            and not any(char.isspace() for char in text)
            and posixpath.splitext(posixpath.basename(text))[0] != "")


def _loads_yaml(node: ast.Call) -> bool:
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if "yaml" in name.lower():
        return True
    return isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "yaml"


def _ref(path: str, spec: str, line: int, lines: Sequence[str], via: str, target: str, **extra) -> dict:
    return {"spec": spec, "line": line, "quote": _quote(lines, line), "via": via,
            "base": posixpath.dirname(path), "target": target, "markers": [], "inferred": True, **extra}


def yaml_refs(path: str, text: str, tree: Optional[ast.AST] = None) -> List[dict]:
    """Path references (see `pathref`) to the YAML files this Python file reads."""
    lowered = text.lower()
    if "yaml" not in lowered and ".yml" not in lowered:
        return []
    if tree is None:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            return []
    lines = text.splitlines()
    inside_fstrings: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            inside_fstrings.update(id(value) for value in node.values)
    refs: List[dict] = []
    seen = set()
    load_line = 0
    named = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _loads_yaml(node):
            load_line = min(load_line or node.lineno, node.lineno)
        elif isinstance(node, ast.JoinedStr):
            literal = "".join(value.value for value in node.values
                              if isinstance(value, ast.Constant) and isinstance(value.value, str))
            if literal.lower().endswith(SUFFIXES):
                named = True
                spec = "".join(value.value if isinstance(value, ast.Constant) else "{}" for value in node.values)
                key = (node.lineno, spec)
                if key not in seen:
                    seen.add(key)
                    refs.append(_ref(path, spec, node.lineno, lines, "python:yaml_path", "file", dynamic=True))
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in inside_fstrings
              and _names_yaml_file(node.value)):
            named = True
            spec = node.value.replace("\\", "/").lstrip("/")
            key = (node.lineno, spec)
            if key in seen:
                continue
            seen.add(key)
            if any(char in spec for char in _GLOB_CHARS):
                refs.append(_ref(path, spec, node.lineno, lines, "python:yaml_glob", "glob", quiet=True))
            else:
                refs.append(_ref(path, spec, node.lineno, lines, "python:yaml_path", "file",
                                 fallback=True, rule="python_yaml"))
    if load_line and not named:
        refs.append(_ref(path, "*.{yaml,yml}", load_line, lines, "python:same_folder_yaml", "glob",
                         quiet=True, rule="same_folder"))
    return sorted(refs, key=lambda ref: (ref["line"], ref["spec"]))
