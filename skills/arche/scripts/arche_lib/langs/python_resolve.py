"""Map Python import records to repository files.

Python decides import targets from sys.path at run time, which a static reader
cannot see. Resolution here follows conventions, nearest first:
  1. the importer's own directory, when that directory is not a package;
  2. source roots (repository root, directories holding pyproject.toml,
     setup.py, setup.cfg or requirements.txt, and their src/ folders);
  3. as a fallback, the single non-package directory anywhere in the repository
     that could provide the top-level name, including namespace packages under
     a nested src/. Such an edge is `inferred`, not `verified`: it assumes a
     sys.path entry nobody declared. Only directories that actually hold the
     imported module count as candidates, so `airflow/` folders of DAGs do not
     capture `airflow.operators.python`.
A top-level name the repository declares as a dependency (requirements*.txt,
pyproject.toml, setup.cfg) is external unless a source root holds a regular
module for it: an installed regular package wins over a local namespace folder.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Callable, Dict, FrozenSet, Iterator, List, NamedTuple, Optional, Sequence, Tuple

from .python_deps import declared_import_names

DYNAMIC_SPEC = "<dynamic>"
INIT_FILE = "__init__.py"
FALLBACK_RULE = "unique-module-name"
ROOT_MARKERS = frozenset({"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"})


class Context(NamedTuple):
    modules: FrozenSet[str]        # parsed .py paths: valid edge targets
    all_paths: FrozenSet[str]      # every path in the inventory
    package_dirs: FrozenSet[str]   # every directory above a .py file, "" included
    roots: Tuple[str, ...]         # source roots; "" is the repository root
    top_level_dirs: Dict[str, Tuple[str, ...]]  # name -> non-package dirs providing it
    declared: FrozenSet[str] = frozenset()  # import names of declared dependencies


def _parent(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return "" if parent == "." else parent


def _join(base: str, parts: Sequence[str]) -> str:
    return "/".join(part for part in (base, *parts) if part)


def _ancestors(directory: str) -> Iterator[str]:
    current = directory
    while current:
        yield current
        current = _parent(current)
    yield ""


def _source_roots(paths: FrozenSet[str]) -> Tuple[str, ...]:
    marker_dirs = {_parent(path) for path in paths if PurePosixPath(path).name in ROOT_MARKERS}
    candidates = marker_dirs | {""}
    src_dirs = {
        _join(directory, ["src"])
        for directory in candidates
        if any(path.startswith(_join(directory, ["src"]) + "/") for path in paths)
    }
    return tuple(sorted(candidates | src_dirs))


def _top_level_dirs(paths: FrozenSet[str]) -> Dict[str, Tuple[str, ...]]:
    """For each module name, the non-package directories it could be imported from."""
    found: Dict[str, set] = {}
    for path in paths:
        if not path.endswith(".py"):
            continue
        is_init = PurePosixPath(path).name == INIT_FILE
        holder = _parent(path)
        name = PurePosixPath(holder).name if is_init else PurePosixPath(path).stem
        directory = _parent(holder) if is_init else holder
        if name and _join(directory, [INIT_FILE]) not in paths:
            found.setdefault(name, set()).add(directory)
        parts = PurePosixPath(path).parts
        for i, part in enumerate(parts[:-2]):
            if part == "src":
                root = "/".join(parts[:i + 1])
                found.setdefault(parts[i + 1], set()).add(root)
    return {name: tuple(sorted(dirs)) for name, dirs in found.items()}


def build_context(
    parsed_paths: Sequence[str],
    all_paths: Sequence[str],
    read_path: Callable[[str], Optional[str]],
) -> Context:
    """`read_path` reads dependency manifests for the declared import names."""
    paths = frozenset(all_paths)
    python_dirs = (_parent(path) for path in paths if path.endswith(".py"))
    return Context(
        modules=frozenset(parsed_paths),
        all_paths=paths,
        package_dirs=frozenset(d for directory in python_dirs for d in _ancestors(directory)),
        roots=_source_roots(paths),
        top_level_dirs=_top_level_dirs(paths),
        declared=declared_import_names(paths, read_path),
    )


def _unresolved(spec: str, reason: str) -> dict:
    return {"status": "unresolved", "spec": spec, "reason": reason}


def _target(path: str, names: Sequence[str], spec: str, ctx: Context) -> dict:
    if path in ctx.modules:
        return {"status": "verified", "to": path, "names": list(names)}
    return _unresolved(spec, "target_skipped")


def _module_file(base: str, parts: Sequence[str], ctx: Context) -> Optional[str]:
    """File that defines module `parts` under directory `base`, if the inventory has it."""
    stem = _join(base, parts)
    candidates = (stem + ".py", _join(stem, [INIT_FILE])) if parts else (_join(base, [INIT_FILE]),)
    return next((candidate for candidate in candidates if candidate in ctx.all_paths), None)


def _spec(imp: dict) -> str:
    return "." * imp["level"] + (imp["module"] or "")


def _sub_spec(spec: str, name: str) -> str:
    return spec + name if spec.endswith(".") or not spec else spec + "." + name


def _is_under(root: str, directory: str) -> bool:
    return root == "" or directory == root or directory.startswith(root + "/")


def _ordered_roots(importer: str, ctx: Context) -> Tuple[str, ...]:
    """Nearest first: the importer's own directory when it is not a package,
    then roots above the importer (deepest first), then the remaining roots."""
    own = _parent(importer)
    is_package = _join(own, [INIT_FILE]) in ctx.all_paths
    above = sorted((root for root in ctx.roots if _is_under(root, own)), key=len, reverse=True)
    others = sorted(root for root in ctx.roots if root not in above)
    ordered = ([] if is_package else [own]) + above + others
    return tuple(dict.fromkeys(ordered))


def _resolve_from(root: str, parts: Sequence[str], names, spec: str, ctx: Context) -> Optional[List[dict]]:
    """Resolve `from <parts> import <names>` under `root`; None when the module is not there."""
    module_file = _module_file(root, parts, ctx)
    if module_file is None and _join(root, parts) not in ctx.package_dirs:
        return None
    submodules = {
        name: _module_file(root, [*parts, name], ctx) for name in names if name != "*"
    }
    results = [
        _target(path, [], _sub_spec(spec, name), ctx)
        for name, path in submodules.items()
        if path is not None
    ]
    plain = [name for name in names if submodules.get(name) is None]
    if plain and module_file is not None:
        results.append(_target(module_file, plain, spec, ctx))
    elif plain:
        results.append(_unresolved(spec, "module_not_found"))
    return results


def _resolve_in_root(root: str, parts: Sequence[str], imp: dict, ctx: Context) -> Optional[List[dict]]:
    if imp["kind"] == "from":
        return _resolve_from(root, parts, imp["names"], _spec(imp), ctx)
    module_file = _module_file(root, parts, ctx)
    if module_file is not None:
        return [_target(module_file, [], imp["module"], ctx)]
    if _join(root, parts) in ctx.package_dirs:
        return [_unresolved(imp["module"], "namespace_package")]
    return None


def _resolve_relative(imp: dict, importer: str, ctx: Context) -> List[dict]:
    spec = _spec(imp)
    base_parts = PurePosixPath(importer).parent.parts
    climb = imp["level"] - 1
    if climb > len(base_parts):
        return [_unresolved(spec, "relative_beyond_root")]
    base = "/".join(base_parts[: len(base_parts) - climb])
    parts = imp["module"].split(".") if imp["module"] else []
    found = _resolve_from(base, parts, imp["names"], spec, ctx)
    return found if found is not None else [_unresolved(spec, "module_not_found")]


def _top_level_known(name: str, roots: Sequence[str], ctx: Context) -> bool:
    return any(
        _module_file(root, [name], ctx) is not None or _join(root, [name]) in ctx.package_dirs
        for root in roots
    )


def _as_inferred(result: dict) -> dict:
    if result["status"] != "verified":
        return result
    return {**result, "status": "inferred", "rule": FALLBACK_RULE}


def _found_locally(results: Sequence[dict]) -> bool:
    """True when some result names a file that defines the imported module."""
    return any(r["status"] == "verified" or r.get("reason") == "target_skipped" for r in results)


def _provides(directory: str, parts: Sequence[str], imp: dict, ctx: Context) -> bool:
    found = _resolve_in_root(directory, parts, imp, ctx)
    return found is not None and (
        _found_locally(found) or any(r.get("reason") == "namespace_package" for r in found)
    )


def _shadows_installed(root: str, parts: Sequence[str], found: Optional[Sequence[dict]], ctx: Context) -> bool:
    """True when `root` holds only a plain folder named like the import's top level
    (no __init__.py, no module file, not under src/) and that folder does not hold
    the imported module. Python would keep searching sys.path, so such a folder,
    like an `airflow/` folder of DAGs, does not hide the installed package."""
    found = found or []
    return (
        _join(root, parts[:1]) in ctx.package_dirs
        and not _found_locally(found)
        and not any(r.get("reason") == "namespace_package" for r in found)
        and _module_file(root, parts[:1], ctx) is None
        and PurePosixPath(root).name != "src"
    )


def _external(parts: Sequence[str]) -> List[dict]:
    return [{"status": "external", "package": parts[0]}]


def _resolve_by_unique_name(parts: Sequence[str], imp: dict, ctx: Context) -> Optional[List[dict]]:
    """Fallback for imports that rely on an undeclared sys.path entry.

    With several candidate directories, keep those that hold the imported module;
    when none does, the local folders merely share a name with an installed package."""
    directories = ctx.top_level_dirs.get(parts[0], ())
    if len(directories) > 1:
        directories = tuple(d for d in directories if _provides(d, parts, imp, ctx))
        if not directories:
            return _external(parts)
    if len(directories) == 1:
        found = _resolve_in_root(directories[0], parts, imp, ctx)
        if _shadows_installed(directories[0], parts, found, ctx):
            return _external(parts)
        return ([_unresolved(imp["module"], "module_not_found")] if found is None
                else [_as_inferred(result) for result in found])
    if len(directories) > 1:
        candidates = sorted(
            _module_file(d, [parts[0]], ctx) or _join(d, [parts[0]]) for d in directories
        )
        return [{**_unresolved(imp["module"], "ambiguous_module"), "candidates": candidates}]
    return None


def resolve(imp: dict, importer: str, ctx: Context) -> List[dict]:
    """Each result is verified or inferred (`to`, `names`), unresolved (`spec`, `reason`)
    or external (`package`)."""
    if imp["module"] is None:
        return [_unresolved(DYNAMIC_SPEC, "dynamic")]
    if imp["level"] > 0:
        return _resolve_relative(imp, importer, ctx)
    parts = imp["module"].split(".")
    roots = _ordered_roots(importer, ctx)
    declared = parts[0] in ctx.declared
    shadowed = False
    for root in roots:
        found = _resolve_in_root(root, parts, imp, ctx)
        if _shadows_installed(root, parts, found, ctx):
            shadowed = True
            continue
        if found is not None and (not declared or _found_locally(found)):
            return found
    if declared:
        return _external(parts)
    fallback = _resolve_by_unique_name(parts, imp, ctx)
    if fallback is not None:
        return fallback
    if shadowed:
        return _external(parts)
    if _top_level_known(parts[0], roots, ctx):
        return [_unresolved(imp["module"], "module_not_found")]
    return _external(parts)
