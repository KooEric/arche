"""Map TypeScript/JavaScript import specifiers to repository files.

Order for a bare specifier: tsconfig `paths`, then `baseUrl`, then a workspace
package (a package.json in this repository with that name), then external.
A workspace entry found through the manifest's own fields is `verified`; one
found only by the src/index convention is `inferred`.
"""
from __future__ import annotations

import posixpath
from typing import Callable, Dict, FrozenSet, List, NamedTuple, Optional, Sequence, Tuple

from .. import jsonc
from .pathutil import join_norm, parent

DYNAMIC_SPEC = "<dynamic>"
WORKSPACE_FALLBACK_RULE = "workspace-source-fallback"
EXTENSIONS = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
JS_TO_TS = ((".js", (".ts", ".tsx")), (".jsx", (".tsx",)), (".mjs", (".mts",)), (".cjs", (".cts",)))
PROTOCOLS = ("node:", "npm:", "jsr:", "http://", "https://", "data:", "bun:", "file:")
CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")
MANIFEST_NAME = "package.json"
MAX_CONFIG_DEPTH = 5
_CONDITIONS = ("types", "source", "import", "default", "require", "module")
_MANIFEST_FIELDS = ("types", "typings", "source", "module", "main")

PathEntry = Tuple[str, Tuple[str, ...], str]   # (pattern, targets, directory targets are relative to)


class Config(NamedTuple):
    base_dir: Optional[str]
    paths: Tuple[PathEntry, ...]


class Package(NamedTuple):
    directory: str
    manifest: dict


class Context(NamedTuple):
    modules: FrozenSet[str]
    all_paths: FrozenSet[str]
    configs: Dict[str, Config]      # directory -> the tsconfig/jsconfig that lives there
    packages: Dict[str, Package]    # package name -> its directory and manifest


ReadPath = Callable[[str], Optional[str]]


# ---------- context ----------

def _path_entries(raw: object, anchor: str) -> Tuple[PathEntry, ...]:
    if not isinstance(raw, dict):
        return ()
    entries = [
        (pattern, tuple(t for t in targets if isinstance(t, str)), anchor)
        for pattern, targets in raw.items()
        if isinstance(targets, list)
    ]
    # Exact patterns first, then the longest prefix, as the TypeScript compiler does.
    return tuple(sorted(entries, key=lambda e: ("*" in e[0], -len(e[0].split("*")[0]), e[0])))


def _linked_configs(data: dict, directory: str, all_paths: FrozenSet[str]) -> List[str]:
    """Configs this one inherits from: relative `extends`, then project `references`."""
    extends = data.get("extends")
    names = [extends] if isinstance(extends, str) else (extends if isinstance(extends, list) else [])
    links = [name if name.endswith(".json") else name + ".json"
             for name in names if isinstance(name, str) and name.startswith(".")]
    for reference in data.get("references") or []:
        target = reference.get("path") if isinstance(reference, dict) else None
        if isinstance(target, str):
            links.append(target if target.endswith(".json") else posixpath.join(target, CONFIG_NAMES[0]))
    resolved = (join_norm(directory, link) for link in links)
    return [path for path in resolved if path is not None and path in all_paths]


def _load_config(path: str, read_path: ReadPath, all_paths: FrozenSet[str], depth: int) -> Optional[Config]:
    data = jsonc.loads(read_path(path))
    if not isinstance(data, dict):
        return None
    options = data.get("compilerOptions") if isinstance(data.get("compilerOptions"), dict) else {}
    directory = parent(path)
    base_url = options.get("baseUrl")
    own_base = join_norm(directory, base_url) if isinstance(base_url, str) else None
    own_paths = _path_entries(options.get("paths"), directory if own_base is None else own_base)
    linked = [] if depth >= MAX_CONFIG_DEPTH else [
        config
        for config in (_load_config(p, read_path, all_paths, depth + 1)
                       for p in _linked_configs(data, directory, all_paths))
        if config is not None
    ]
    base = own_base if own_base is not None else next((c.base_dir for c in linked if c.base_dir is not None), None)
    paths = own_paths or next((c.paths for c in linked if c.paths), ())
    return Config(base_dir=base, paths=paths)


def _configs(all_paths: FrozenSet[str], read_path: ReadPath) -> Dict[str, Config]:
    found: Dict[str, Config] = {}
    for name in reversed(CONFIG_NAMES):          # tsconfig.json is applied last and wins
        for path in sorted(p for p in all_paths if posixpath.basename(p) == name):
            config = _load_config(path, read_path, all_paths, 0)
            if config is not None:
                found[parent(path)] = config
    return found


def _packages(all_paths: FrozenSet[str], read_path: ReadPath) -> Dict[str, Package]:
    found: Dict[str, Package] = {}
    manifests = sorted(
        p for p in all_paths
        if posixpath.basename(p) == MANIFEST_NAME and "node_modules" not in p.split("/")
    )
    for path in manifests:
        manifest = jsonc.loads(read_path(path))
        name = manifest.get("name") if isinstance(manifest, dict) else None
        if isinstance(name, str) and name:
            found.setdefault(name, Package(directory=parent(path), manifest=manifest))
    return found


def build_context(parsed_paths: Sequence[str], all_paths: Sequence[str], read_path: ReadPath) -> Context:
    paths = frozenset(all_paths)
    return Context(
        modules=frozenset(parsed_paths),
        all_paths=paths,
        configs=_configs(paths, read_path),
        packages=_packages(paths, read_path),
    )


# ---------- resolution ----------

def _unresolved(spec: str, reason: str) -> dict:
    return {"status": "unresolved", "spec": spec, "reason": reason}


def _found(path: str, imp: dict, ctx: Context) -> dict:
    if path in ctx.modules:
        return {"status": "verified", "to": path, "names": list(imp["names"])}
    return _unresolved(imp["spec"], "target_skipped")


def _find_file(stem: Optional[str], ctx: Context) -> Optional[str]:
    """The file a specifier path refers to, in the order the TypeScript compiler tries:
    the TypeScript source behind a compiled extension, the exact file, an added
    extension, then a directory index."""
    if stem is None:
        return None
    swapped = [stem[: -len(js)] + ts for js, ts_list in JS_TO_TS if stem.endswith(js) for ts in ts_list]
    with_extension = [stem + ext for ext in EXTENSIONS]
    index_files = [posixpath.join(stem, "index" + ext) for ext in EXTENSIONS]
    candidates = swapped + [stem] + with_extension + index_files
    return next((c for c in candidates if c in ctx.all_paths), None)


def _config_for(importer: str, ctx: Context) -> Optional[Config]:
    directory = parent(importer)
    while True:
        if directory in ctx.configs:
            return ctx.configs[directory]
        if not directory:
            return None
        directory = parent(directory)


def _match(pattern: str, spec: str) -> Optional[str]:
    """The text a `*` in the pattern stands for; "" for an exact match; None for no match."""
    if "*" not in pattern:
        return "" if pattern == spec else None
    prefix, suffix = pattern.split("*", 1)
    if spec.startswith(prefix) and spec.endswith(suffix) and len(spec) >= len(prefix) + len(suffix):
        return spec[len(prefix): len(spec) - len(suffix)]
    return None


def _by_config(spec: str, imp: dict, config: Config, ctx: Context) -> Optional[List[dict]]:
    matched_alias = False
    for pattern, targets, anchor in config.paths:
        star = _match(pattern, spec)
        if star is None:
            continue
        for target in targets:
            path = _find_file(join_norm(anchor, target.replace("*", star)), ctx)
            if path is not None:
                return [_found(path, imp, ctx)]
        matched_alias = matched_alias or pattern != "*"
    if matched_alias:
        return [_unresolved(spec, "alias_no_match")]
    if config.base_dir is not None:
        path = _find_file(join_norm(config.base_dir, spec), ctx)
        if path is not None:
            return [_found(path, imp, ctx)]
    return None


def _package_name(spec: str) -> str:
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") else parts[0]


def _condition_targets(entry: object) -> List[str]:
    if isinstance(entry, str):
        return [entry]
    if isinstance(entry, dict):
        return [t for condition in _CONDITIONS for t in _condition_targets(entry.get(condition))]
    return []


def _declared_entries(manifest: dict, subpath: str) -> List[str]:
    """Entry files the manifest itself names for "." or "./<subpath>"."""
    key = "." + subpath
    exports = manifest.get("exports")
    declared: List[str] = []
    if isinstance(exports, dict):
        keyed = any(name.startswith(".") for name in exports)
        declared.extend(_condition_targets(exports.get(key) if keyed else (exports if key == "." else None)))
    elif isinstance(exports, str) and key == ".":
        declared.append(exports)
    if key == ".":
        declared.extend(v for v in (manifest.get(f) for f in _MANIFEST_FIELDS) if isinstance(v, str))
    return declared


def _by_workspace(spec: str, name: str, imp: dict, ctx: Context) -> List[dict]:
    package = ctx.packages[name]
    subpath = spec[len(name):]
    for declared in _declared_entries(package.manifest, subpath):
        path = _find_file(join_norm(package.directory, declared), ctx)
        if path is not None:
            return [_found(path, imp, ctx)]
    tail = subpath.lstrip("/")
    for guess in ([tail, "src/" + tail] if tail else ["src/index", "index"]):
        path = _find_file(join_norm(package.directory, guess), ctx)
        if path is not None:
            result = _found(path, imp, ctx)
            if result["status"] == "verified":
                return [{**result, "status": "inferred", "rule": WORKSPACE_FALLBACK_RULE}]
            return [result]
    return [_unresolved(spec, "workspace_package")]


def _external_name(spec: str) -> str:
    """Name for the externals list: protocol kept, version and subpath dropped."""
    for protocol in ("npm:", "jsr:"):
        if spec.startswith(protocol):
            name = _package_name(spec[len(protocol):])
            version = name.find("@", 1)
            return protocol + (name if version == -1 else name[:version])
    return spec


def resolve(imp: dict, importer: str, ctx: Context) -> List[dict]:
    """Each result is verified or inferred (`to`, `names`), unresolved (`spec`, `reason`)
    or external (`package`)."""
    spec = imp["spec"]
    if spec is None:
        return [_unresolved(DYNAMIC_SPEC, "dynamic")]
    if spec.startswith(PROTOCOLS):
        return [{"status": "external", "package": _external_name(spec)}]
    if spec.startswith("#"):
        return [_unresolved(spec, "subpath_import")]
    if spec.startswith("/"):
        return [_unresolved(spec, "absolute_path")]
    if spec in (".", "..") or spec.startswith(("./", "../")):
        stem = join_norm(parent(importer), spec)
        if stem is None:
            return [_unresolved(spec, "outside_root")]
        path = _find_file(stem, ctx)
        return [_found(path, imp, ctx) if path is not None else _unresolved(spec, "module_not_found")]
    config = _config_for(importer, ctx)
    by_config = _by_config(spec, imp, config, ctx) if config is not None else None
    if by_config is not None:
        return by_config
    name = _package_name(spec)
    if name in ctx.packages:
        return _by_workspace(spec, name, imp, ctx)
    return [{"status": "external", "package": name}]
