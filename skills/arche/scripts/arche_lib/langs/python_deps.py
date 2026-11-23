"""Top-level import names of the third-party packages a repository declares.

Reads requirements*.txt, pyproject.toml (PEP 621 and Poetry tables) and
setup.cfg install_requires with plain line scanning, because tomllib needs
Python 3.11 and the skill supports 3.9. A declared distribution name maps to
the import name it usually provides: the normalized name itself, a few
well-known renames, and the name without an `apache-` or `python-` prefix.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Callable, FrozenSet, Iterable, Iterator, Optional

_REQUIREMENT_NAME = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_QUOTED = re.compile(r"""["']([^"']+)["']""")
_TABLE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_KEY = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*=")
_STRIP_PREFIXES = ("apache_", "python_", "py_")

# Distributions whose import name differs from the distribution name.
_IMPORT_NAMES = {
    "apache_airflow": "airflow",
    "beautifulsoup4": "bs4",
    "pyyaml": "yaml",
    "scikit_learn": "sklearn",
    "pillow": "PIL",
    "python_dateutil": "dateutil",
    "opencv_python": "cv2",
    "opencv_python_headless": "cv2",
    "psycopg2_binary": "psycopg2",
    "protobuf": "google",
    "pyjwt": "jwt",
    "attrs": "attr",
    "msgpack_python": "msgpack",
    "python_dotenv": "dotenv",
}
_IMPORT_NAME_PREFIXES = (("apache_airflow_", "airflow"), ("google_cloud_", "google"), ("google_api_", "google"))


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def import_names(distribution: str) -> FrozenSet[str]:
    """Import names a distribution plausibly provides."""
    norm = _normalize(distribution)
    names = {norm}
    if norm in _IMPORT_NAMES:
        names.add(_IMPORT_NAMES[norm])
    names.update(target for prefix, target in _IMPORT_NAME_PREFIXES if norm.startswith(prefix))
    names.update(norm[len(prefix):] for prefix in _STRIP_PREFIXES if norm.startswith(prefix) and len(norm) > len(prefix))
    return frozenset(names)


def _requirement(line: str) -> Optional[str]:
    text = line.split("#", 1)[0].strip()
    if not text or text.startswith("-") or _URL.match(text):
        return None
    match = _REQUIREMENT_NAME.match(text)
    return match.group(1) if match else None


def _from_requirements(text: str) -> Iterator[str]:
    for line in text.splitlines():
        name = _requirement(line)
        if name:
            yield name


def _quoted_requirements(text: str) -> Iterator[str]:
    for quoted in _QUOTED.findall(text):
        name = _requirement(quoted)
        if name:
            yield name


def _closes_list(text: str) -> bool:
    return "]" in _QUOTED.sub("", text.split("#", 1)[0])


def _is_dependency_list(table: str, key: str) -> bool:
    return (table == "project" and key == "dependencies") or table in (
        "project.optional-dependencies", "dependency-groups"
    )


def _from_pyproject(text: str) -> Iterator[str]:
    table = ""
    collecting = False
    for line in text.splitlines():
        header = _TABLE.match(line)
        if header:
            table, collecting = header.group(1).strip(), False
            continue
        if collecting:
            yield from _quoted_requirements(line)
            collecting = not _closes_list(line)
            continue
        key = _KEY.match(line)
        if key is None:
            continue
        if _is_dependency_list(table, key.group(1)):
            rest = line.split("=", 1)[1]
            yield from _quoted_requirements(rest)
            collecting = not _closes_list(rest)
        elif _is_poetry_dependency_table(table) and _normalize(key.group(1)) != "python":
            yield key.group(1)


def _is_poetry_dependency_table(table: str) -> bool:
    return table in ("tool.poetry.dependencies", "tool.poetry.dev-dependencies") or bool(
        re.fullmatch(r"tool\.poetry\.group\.[^.]+\.dependencies", table)
    )


def _from_setup_cfg(text: str) -> Iterator[str]:
    section = ""
    in_requires = False
    for line in text.splitlines():
        header = _TABLE.match(line)
        if header:
            section, in_requires = header.group(1).strip(), False
            continue
        key = _KEY.match(line)
        if key and not line[:1].isspace():
            in_requires = (section == "options" and key.group(1) == "install_requires") or (
                section == "options.extras_require"
            )
            line = line.split("=", 1)[1]
        if in_requires:
            name = _requirement(line)
            if name:
                yield name


def _readers(path: str) -> Optional[Callable[[str], Iterable[str]]]:
    name = PurePosixPath(path).name
    if name == "pyproject.toml":
        return _from_pyproject
    if name == "setup.cfg":
        return _from_setup_cfg
    if name.startswith("requirements") and name.endswith((".txt", ".in")):
        return _from_requirements
    return None


def declared_import_names(paths: Iterable[str], read_path: Callable[[str], Optional[str]]) -> FrozenSet[str]:
    """Top-level import names of every dependency declared anywhere in the repository."""
    names = set()
    for path in sorted(paths):
        reader = _readers(path)
        if reader is None:
            continue
        text = read_path(path)
        if text is None:
            continue
        for distribution in reader(text):
            names.update(import_names(distribution))
    return frozenset(names)
