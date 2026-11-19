"""Map Dart import, export and part URIs to repository files.

`package:<name>/x.dart` points into `<dir>/lib/x.dart` when a pubspec.yaml in
this repository declares that name; otherwise the package is external.
"""
from __future__ import annotations

import posixpath
import re
from typing import Callable, Dict, FrozenSet, List, NamedTuple, Optional, Sequence

from .pathutil import join_norm, parent

PUBSPEC_NAME = "pubspec.yaml"
PACKAGE_SCHEME = "package:"
_PACKAGE_NAME = re.compile(r"^name:\s*['\"]?([A-Za-z_][\w]*)", re.M)
_SCHEME = re.compile(r"[A-Za-z][\w+.-]*:")


class Context(NamedTuple):
    modules: FrozenSet[str]
    all_paths: FrozenSet[str]
    packages: Dict[str, str]    # package name -> directory holding its pubspec.yaml


def build_context(
    parsed_paths: Sequence[str],
    all_paths: Sequence[str],
    read_path: Callable[[str], Optional[str]],
) -> Context:
    paths = frozenset(all_paths)
    packages: Dict[str, str] = {}
    for path in sorted(p for p in paths if posixpath.basename(p) == PUBSPEC_NAME):
        match = _PACKAGE_NAME.search(read_path(path) or "")
        if match:
            packages.setdefault(match.group(1), parent(path))
    return Context(modules=frozenset(parsed_paths), all_paths=paths, packages=packages)


def _unresolved(spec: str, reason: str) -> dict:
    return {"status": "unresolved", "spec": spec, "reason": reason}


def _target(uri: str, importer: str, ctx: Context) -> Optional[str]:
    """Repository path a non-external URI refers to; None when it leaves the repository."""
    if uri.startswith(PACKAGE_SCHEME):
        name, _, rest = uri[len(PACKAGE_SCHEME):].partition("/")
        return join_norm(ctx.packages[name], posixpath.join("lib", rest))
    return join_norm(parent(importer), uri)


def _external(uri: str, ctx: Context) -> Optional[str]:
    """Name for the externals list, or None when the URI points into this repository."""
    if uri.startswith(PACKAGE_SCHEME):
        name = uri[len(PACKAGE_SCHEME):].partition("/")[0]
        return None if name in ctx.packages else PACKAGE_SCHEME + name
    return uri if _SCHEME.match(uri) else None


def resolve(imp: dict, importer: str, ctx: Context) -> List[dict]:
    """Each result is verified (`to`, `names`), unresolved (`spec`, `reason`) or external."""
    uri = imp["uri"]
    if uri is None:
        return [_unresolved(imp.get("library") or "", "library_name")]
    external = _external(uri, ctx)
    if external is not None:
        return [{"status": "external", "package": external}]
    path = _target(uri, importer, ctx)
    if path is None:
        return [_unresolved(uri, "outside_root")]
    if path in ctx.modules:
        return [{"status": "verified", "to": path, "names": list(imp["names"])}]
    return [_unresolved(uri, "target_skipped" if path in ctx.all_paths else "module_not_found")]
