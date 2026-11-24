"""Paths written in known YAML files: Docker Compose, GitHub Actions and Helm charts.

`references(path, documents, lines)` returns (path_refs, externals, consumed) where
path_refs follow `pathref`, externals are {"package", "line"}, and consumed holds
the (key path, line) pairs a schema read, so the SQL path scan can skip them.
Every YAML file, known or not, is also scanned for SQL file paths by `yaml.py`.
"""
from __future__ import annotations

import fnmatch
import posixpath
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from .yaml_reader import Scalar

MAX_QUOTE_CHARS = 200
_COMPOSE = ("compose.yml", "compose.yaml", "compose.*.yml", "compose.*.yaml",
            "docker-compose.yml", "docker-compose.yaml", "docker-compose.*.yml", "docker-compose.*.yaml")
_ACTION_FILES = ("action.yml", "action.yaml")
_HELM_DEPENDENCY_FILES = ("Chart.yaml", "requirements.yaml")
_REMOTE = ("://", "git@")

Found = Tuple[List[dict], List[dict], Set[Tuple[tuple, int]]]


def _matches(key: tuple, pattern: Sequence[str]) -> bool:
    """`*` matches any mapping key, `[]` any sequence position."""
    if len(key) != len(pattern):
        return False
    for part, want in zip(key, pattern):
        if want == "[]":
            if not isinstance(part, int):
                return False
        elif want == "*":
            if not isinstance(part, str):
                return False
        elif part != want:
            return False
    return True


class _Collector:
    def __init__(self, path: str, lines: Sequence[str], schema: str):
        self.base = posixpath.dirname(path)
        self.lines = lines
        self.schema = schema
        self.refs: List[dict] = []
        self.externals: List[dict] = []
        self.consumed: Set[Tuple[tuple, int]] = set()

    def quote(self, line: int) -> str:
        return self.lines[line - 1].strip()[:MAX_QUOTE_CHARS] if 1 <= line <= len(self.lines) else ""

    def ref(self, item: Scalar, pattern: str, spec: str, target: str, base: Optional[str] = None,
            markers: Sequence[str] = ()) -> None:
        key, _, line = item
        self.consumed.add((key, line))
        self.refs.append({"spec": spec, "line": line, "quote": self.quote(line), "via": f"{self.schema}:{pattern}",
                          "base": self.base if base is None else base, "target": target, "markers": list(markers)})

    def external(self, item: Scalar, package: str) -> None:
        key, _, line = item
        self.consumed.add((key, line))
        self.externals.append({"package": package, "line": line})


def _is_remote(value: str) -> bool:
    return any(marker in value for marker in _REMOTE)


def _compose(c: _Collector, scalars: Sequence[Scalar]) -> None:
    contexts: Dict[str, str] = {}
    for item in scalars:
        key, value, _ = item
        if _matches(key, ("services", "*", "build")) or _matches(key, ("services", "*", "build", "context")):
            contexts[key[1]] = value
    for item in scalars:
        key, value, _ = item
        if _matches(key, ("services", "*", "build")) or _matches(key, ("services", "*", "build", "context")):
            pattern = "services.*.build" if len(key) == 3 else "services.*.build.context"
            if _is_remote(value):
                c.external(item, value.split("#", 1)[0])
            else:
                c.ref(item, pattern, value, "dir", markers=("Dockerfile",))
        elif _matches(key, ("services", "*", "build", "dockerfile")):
            context = contexts.get(key[1], ".")
            if not _is_remote(context):
                c.ref(item, "services.*.build.dockerfile", value, "file",
                      base=posixpath.normpath(posixpath.join(c.base, context)))
        elif (_matches(key, ("services", "*", "env_file")) or _matches(key, ("services", "*", "env_file", "[]"))
              or _matches(key, ("services", "*", "env_file", "[]", "path"))):
            c.ref(item, "services.*.env_file", value, "file")
        elif _matches(key, ("services", "*", "extends", "file")):
            c.ref(item, "services.*.extends.file", value, "file")
        elif _matches(key, ("services", "*", "volumes", "[]")):
            host = value.split(":", 1)[0]
            if host.startswith(("./", "../")) or host in (".", ".."):
                c.ref(item, "services.*.volumes", host, "any")
        elif _matches(key, ("services", "*", "volumes", "[]", "source")):
            if value.startswith(("./", "../")):
                c.ref(item, "services.*.volumes.source", value, "any")
        elif (_matches(key, ("include", "[]")) or _matches(key, ("include", "[]", "path"))
              or _matches(key, ("include", "[]", "path", "[]"))):
            c.ref(item, "include", value, "file")


def _uses_package(value: str) -> str:
    """`actions/checkout@v4` -> `actions/checkout`; `docker://alpine:3.19` -> `docker://alpine`."""
    if value.startswith("docker://"):
        image = value[len("docker://"):].split("@", 1)[0]
        name, _, tag = image.rpartition(":")
        return "docker://" + (name if name and "/" not in tag else image)
    return "/".join(value.split("@", 1)[0].split("/")[:2])


def _actions(c: _Collector, scalars: Sequence[Scalar]) -> None:
    patterns = {("jobs", "*", "uses"): "jobs.*.uses", ("jobs", "*", "steps", "[]", "uses"): "jobs.*.steps.uses",
                ("runs", "steps", "[]", "uses"): "runs.steps.uses"}
    for item in scalars:
        key, value, _ = item
        pattern = next((name for shape, name in patterns.items() if _matches(key, shape)), None)
        if pattern is None or "${{" in value:
            continue
        if value.startswith("./"):
            c.ref(item, pattern, value, "any", base="", markers=_ACTION_FILES)
        else:
            c.external(item, _uses_package(value))


def _helm(c: _Collector, scalars: Sequence[Scalar]) -> None:
    names = {key[1]: value for key, value, _ in scalars if _matches(key, ("dependencies", "[]", "name"))}
    for item in scalars:
        key, value, _ = item
        if not _matches(key, ("dependencies", "[]", "repository")):
            continue
        if value.startswith("file://"):
            c.ref(item, "dependencies.repository", value[len("file://"):], "dir", markers=("Chart.yaml",))
        elif value and not value.startswith("@") and not value.startswith("alias:"):
            name = names.get(key[1])
            c.external(item, value.rstrip("/") + ("/" + name if name else ""))


def _looks_like_compose(documents: Sequence[Sequence[Scalar]]) -> bool:
    """A top-level `services` mapping whose entries have an image or a build: Compose
    files named after their purpose (`base.yml`, `backend-postgres.yml`) are common."""
    return any(len(key) >= 3 and key[0] == "services" and isinstance(key[1], str) and key[2] in ("image", "build")
               for document in documents for key, _, _ in document)


def schema_for(path: str, documents: Sequence[Sequence[Scalar]] = ()) -> Optional[Tuple[str, Callable]]:
    name = posixpath.basename(path)
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in _COMPOSE):
        return "compose", _compose
    if (path.startswith(".github/workflows/") and name.endswith((".yml", ".yaml"))) or name in _ACTION_FILES:
        return "actions", _actions
    if name in _HELM_DEPENDENCY_FILES:
        return "helm", _helm
    if _looks_like_compose(documents):
        return "compose", _compose
    return None


def references(path: str, documents: Sequence[Sequence[Scalar]], lines: Sequence[str]) -> Found:
    chosen = schema_for(path, documents)
    if chosen is None:
        return [], [], set()
    schema, read = chosen
    collector = _Collector(path, lines, schema)
    for document in documents:
        read(collector, document)
    return collector.refs, collector.externals, collector.consumed
