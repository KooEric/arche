"""dbt projects: link `{{ ref('model') }}` and property-file model names to model files.

A project is a directory holding dbt_project.yml. Its models and snapshots are the
parsed .sql files under its model-paths and snapshot-paths, named by file stem;
seeds are .csv files under seed-paths (not parsed, so a ref to one is
target_skipped). A ref is looked up in the project that holds the SQL file, then
in the local packages its packages.yml lists. A ref naming another package that
is not local is external. Model names are unique within a dbt project, so a
single match is verified.
"""
from __future__ import annotations

import posixpath
import re
from typing import Dict, Iterable, List, NamedTuple, Optional

from .langs.yaml_dbt import PROJECT_FILE
from .langs.yaml_schemas import DBT_PACKAGE_FILES

RULE = "dbt_ref"
VERSION_RULE = "dbt_latest_version"
NAME_RULE = "dbt_name"


class Project(NamedTuple):
    directory: str
    name: Optional[str]
    packages: List[str]              # directories of local packages
    names: Dict[str, List[str]]      # model, snapshot or seed name -> files


def _under(path: str, directory: str) -> bool:
    return directory == "" or path.startswith(directory + "/")


def build(inventory_paths: Iterable[str], extracted: Dict[str, dict]) -> Dict[str, Project]:
    """Project directory -> Project, for every dbt_project.yml the scan parsed."""
    paths = sorted(inventory_paths)
    projects: Dict[str, Project] = {}
    for path, result in extracted.items():
        if posixpath.basename(path) != PROJECT_FILE or "dbt_project" not in result:
            continue
        directory = posixpath.dirname(path)
        settings = result["dbt_project"]
        packages: List[str] = []
        for file_name in DBT_PACKAGE_FILES:
            packages += extracted.get(posixpath.join(directory, file_name), {}).get("dbt_local_packages", [])
        names: Dict[str, List[str]] = {}
        roots = [(posixpath.normpath(posixpath.join(directory, folder)), ".sql")
                 for key in ("model-paths", "snapshot-paths") for folder in settings[key]]
        roots += [(posixpath.normpath(posixpath.join(directory, folder)), ".csv") for folder in settings["seed-paths"]]
        for root, suffix in roots:
            root = "" if root == "." else root
            for candidate in paths:
                if candidate.endswith(suffix) and _under(candidate, root) and (suffix == ".csv" or candidate in extracted):
                    stem = posixpath.splitext(posixpath.basename(candidate))[0]
                    if candidate not in names.setdefault(stem, []):
                        names[stem].append(candidate)
        projects[directory] = Project(directory, settings.get("name"), packages, names)
    return projects


def project_of(path: str, projects: Dict[str, Project]) -> Optional[Project]:
    directory = posixpath.dirname(path)
    while True:
        if directory in projects:
            return projects[directory]
        if not directory:
            return None
        directory = posixpath.dirname(directory)


def _unresolved(spec: str, reason: str, candidates: List[str] = ()) -> dict:
    return {"status": "unresolved", "spec": spec, "reason": reason,
            **({"candidates": list(candidates)} if candidates else {})}


def _versions(name: str, project: Project) -> Dict[int, List[str]]:
    """Files of a versioned model: `orders_v1.sql`, `orders_v2.sql` for `orders`."""
    pattern = re.compile(re.escape(name) + r"_v(\d+)$")
    found: Dict[int, List[str]] = {}
    for stem, files in project.names.items():
        match = pattern.match(stem)
        if match:
            found.setdefault(int(match.group(1)), []).extend(files)
    return found


def _lookup(name: str, order: List[Project], parsed, rule: str, version: Optional[str] = None) -> Optional[dict]:
    """A name, or a versioned model's file: the asked version is verified; without one,
    the highest version is inferred, since a property file may pin latest_version."""
    for project in order:
        hits = project.names.get(name, []) if version is None else []
        status, chosen_rule = "verified", rule
        if not hits:
            versions = _versions(name, project)
            if version is not None:
                hits = versions.get(int(version), [])
            elif versions:
                hits, status, chosen_rule = versions[max(versions)], "inferred", VERSION_RULE
        if len(hits) == 1:
            if hits[0] in parsed:
                return {"status": status, "to": hits[0], "rule": chosen_rule}
            return _unresolved(name, "target_skipped")
        if hits:
            return _unresolved(name, "ambiguous", hits)
    return None


def resolve_ref(ref: dict, importer: str, projects: Dict[str, Project], parsed) -> dict:
    """verified (rule dbt_ref), unresolved (dynamic|no_dbt_project|ambiguous|target_skipped|not_found) or external."""
    if ref["dynamic"]:
        return _unresolved(ref["quote"], "dynamic")
    own = project_of(importer, projects)
    if own is None:
        return _unresolved(ref["ref"], "no_dbt_project")
    local = [projects[d] for d in own.packages if d in projects]
    package = ref.get("package")
    if package and package != own.name:
        order = [project for project in local if project.name == package]
        if not order:
            return {"status": "external", "package": package}
    else:
        order = [own] + local
    return _lookup(ref["ref"], order, parsed, RULE, ref.get("version")) or _unresolved(ref["ref"], "not_found")


def resolve_name(item: dict, importer: str, projects: Dict[str, Project], parsed) -> Optional[dict]:
    """The model file a property file names, or None outside a dbt project."""
    own = project_of(importer, projects)
    if own is None:
        return None
    return _lookup(item["name"], [own], parsed, NAME_RULE) or _unresolved(item["name"], "not_found")
