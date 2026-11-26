"""dbt projects: link `{{ ref('model') }}` and property-file model names to model files.

A project is a directory holding dbt_project.yml. Its models and snapshots are the
parsed .sql files under its model-paths and snapshot-paths, named by file stem;
seeds are .csv files under seed-paths (not parsed, so a ref to one is
target_skipped). A ref is looked up in the project that holds the SQL file, then
in the local packages its packages.yml lists. A ref naming another package that
is not local is external. Model names are unique within a dbt project, so a
single match is verified.

Each model and snapshot file also names the table it builds: an alias (config,
then the deepest folder setting, then the file name) under a schema the target
chooses at run time, `{}` or `{}_<schema>` as dbt's default generate_schema_name
does. `source()` reads take their real names from sources.yml.
"""
from __future__ import annotations

import posixpath
import re
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

from .langs.yaml_dbt import PROJECT_FILE
from .langs.yaml_schemas import DBT_PACKAGE_FILES

RULE = "dbt_ref"
VERSION_RULE = "dbt_latest_version"
NAME_RULE = "dbt_name"


MODEL_VIA = "dbt:model"
_PLAIN_NAME = re.compile(r"[\w$-]+")


class Project(NamedTuple):
    directory: str
    name: Optional[str]
    packages: List[str]              # directories of local packages
    names: Dict[str, List[str]]      # model, snapshot or seed name -> files
    roots: List[Tuple[str, str]] = []   # (directory, "models" | "snapshots") holding model SQL
    folders: List[dict] = []            # dbt_project.yml folder settings
    sources: Dict[Tuple[str, str], List[dict]] = {}  # (source, table) -> definitions


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
        model_roots = [("" if root == "." else root, kind) for kind, key in (("models", "model-paths"), ("snapshots", "snapshot-paths"))
                       for root in (posixpath.normpath(posixpath.join(directory, folder)) for folder in settings[key])]
        projects[directory] = Project(directory, settings.get("name"), packages, names, model_roots,
                                      result.get("dbt_folder_config", []))
    return _with_sources(projects, extracted)


def _with_sources(projects: Dict[str, Project], extracted: Dict[str, dict]) -> Dict[str, Project]:
    """Each project with the source tables its own property files define."""
    found: Dict[str, Dict[Tuple[str, str], List[dict]]] = {directory: {} for directory in projects}
    for path in sorted(extracted):
        own = project_of(path, projects) if extracted[path].get("dbt_props") else None
        if own is None:
            continue
        for source in extracted[path]["dbt_props"]["sources"]:
            for table in source["tables"]:
                found[own.directory].setdefault((source["name"], table["name"]), []).append(
                    {"path": path, "source": source, "table": table})
    return {directory: project._replace(sources=found[directory]) for directory, project in projects.items()}


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


def _lower(value: Optional[str]) -> Optional[str]:
    return None if value is None else value.strip().lower()


def _deepest(folders: List[dict], kind: str, parts: List[str], setting: str) -> Optional[dict]:
    """The folder setting for the deepest folder that holds the model."""
    best = None
    for item in folders:
        if item["kind"] == kind and item["setting"] == setting and parts[:len(item["folders"])] == item["folders"]:
            if best is None or len(item["folders"]) >= len(best["folders"]):
                best = item
    return best


def model_table(path: str, project: Project, extracted: Dict[str, dict]) -> Optional[dict]:
    """{"kind", "table", "dynamic", "alias_from", "schema_from", "line"} for a model or snapshot file, else None.

    `table` is None when the alias is not a literal or the config call sits in a branch."""
    for root, kind in project.roots:
        if path.endswith(".sql") and _under(path, root) and path in extracted:
            break
    else:
        return None
    parts = posixpath.dirname(path[len(root) + 1:] if root else path).split("/")
    parts = [part for part in parts if part]
    config = extracted[path].get("dbt_config")
    alias, alias_from, dynamic = posixpath.splitext(posixpath.basename(path))[0], "default", False
    folder_alias = _deepest(project.folders, kind, parts, "alias")
    if config and ("alias" in config["dynamic"] or config["alias"] is not None):
        alias, alias_from, dynamic = config["alias"], "config", "alias" in config["dynamic"]
    elif folder_alias:
        alias, alias_from, dynamic = folder_alias["value"], "project", "{{" in folder_alias["value"]
    schema, schema_from = None, "default"
    folder_schema = _deepest(project.folders, kind, parts, "schema")
    if config and ("schema" in config["dynamic"] or config["schema"] is not None):
        schema, schema_from = config["schema"], "config"
    elif folder_schema:
        schema, schema_from = folder_schema["value"], "project"
    alias, schema = _lower(alias), _lower(schema)
    dynamic = dynamic or bool(config and config["branched"]) or not alias or not _PLAIN_NAME.fullmatch(alias)
    if schema is not None and ("{{" in schema or not _PLAIN_NAME.fullmatch(schema)):
        schema = None
    table = None if dynamic else ("{}." if schema is None else "{}_" + schema + ".") + alias
    return {"kind": kind, "table": table, "dynamic": dynamic, "alias_from": alias_from, "schema_from": schema_from,
            "line": config["line"] if config else 1}


def models(projects: Dict[str, Project], extracted: Dict[str, dict]) -> List[dict]:
    """[{"file", "project", "model", **model_table}] for every model and snapshot file, by file."""
    found: Dict[str, dict] = {}
    paths = sorted(extracted)
    for directory in sorted(projects):
        project = projects[directory]
        for path in paths:
            if path in found:
                continue
            table = model_table(path, project, extracted)
            if table:
                found[path] = {"file": path, "project": project.name,
                               "model": posixpath.splitext(posixpath.basename(path))[0], **table}
    return [found[path] for path in sorted(found)]


def _part(value: Optional[str], default: str) -> str:
    """One part of a source table name; `{}` when it is set at run time."""
    if value is None:
        return default.lower()
    return "{}" if "{{" in value or not _PLAIN_NAME.fullmatch(value.strip()) else value.strip().lower()


def resolve_source(names: List[str], importer: str, projects: Dict[str, Project]) -> Optional[dict]:
    """{"status": "resolved" | "ambiguous", "table", "definitions"} for `source(a, b)`, or None
    without a definition in the importer's project or its local packages."""
    own = project_of(importer, projects)
    if own is None or len(names) != 2:
        return None
    for project in [own] + [projects[d] for d in own.packages if d in projects]:
        definitions = project.sources.get((names[0], names[1]), [])
        if len(definitions) > 1:
            return {"status": "ambiguous", "table": f"{names[0]}.{names[1]}".lower(), "definitions": definitions}
        if definitions:
            found = definitions[0]
            source, table = found["source"], found["table"]
            parts = ([_part(source["database"], "")] if source["database"] is not None else []) + [
                _part(source["schema"], source["name"]), _part(table["identifier"], table["name"])]
            return {"status": "resolved", "table": ".".join(parts), "definitions": definitions}
    return None
