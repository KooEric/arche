"""Build graph.json content: file and symbol nodes plus IMPORTS, REFERENCES and READS edges."""
from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple

from . import SCHEMA_VERSION, dbt, fileref, lineage, pathref
from .errors import ParseError
from .httpmatch import connect, join_path
from .jsonio import inventory_digest
from .langs.registry import adapter_for

ReadPath = Callable[[str], Optional[str]]
_FLAGS = ("conditional", "type_only", "dynamic", "reexport", "part")
_RESOLVED = ("verified", "inferred")


def _file_id(path: str) -> str:
    return "file:" + path


def _extract_all(analyzed: List[dict], read_path: ReadPath) -> Tuple[Dict[str, dict], List[str]]:
    """Parse every analyzed file. A file that fails to parse is reported, not fatal."""
    extracted: Dict[str, dict] = {}
    failures: List[str] = []
    for entry in analyzed:
        path = entry["path"]
        text = read_path(path)
        try:
            if text is None:
                raise ParseError(f"{path}: not readable")
            extracted[path] = adapter_for(entry["lang"]).extract(path, text)
        except ParseError:
            failures.append(path)
    return extracted, failures


def _contexts(analyzed: List[dict], extracted: Dict[str, dict], inventory: dict, read_path: ReadPath,
              options: Dict[str, list]) -> dict:
    """One context per adapter, shared by every language that adapter handles."""
    all_paths = [entry["path"] for entry in inventory["files"]]
    langs = sorted({entry["lang"] for entry in analyzed})
    by_adapter: Dict[str, object] = {}
    for lang in langs:
        adapter = adapter_for(lang)
        if adapter.__name__ in by_adapter:
            continue
        handled = {name for name in langs if adapter_for(name) is adapter}
        parsed = [e["path"] for e in analyzed if e["lang"] in handled and e["path"] in extracted]
        given = {name: options[name] for name in getattr(adapter, "OPTIONS", ()) if name in options}
        by_adapter[adapter.__name__] = adapter.build_context(parsed, all_paths, read_path, **given)
    return {lang: by_adapter[adapter_for(lang).__name__] for lang in langs}


def _edge(importer: str, imp: dict, result: dict) -> dict:
    base = {
        "kind": "IMPORTS",
        "from": _file_id(importer),
        "origin": "script",
        "status": result["status"],
        "evidence": {"path": importer, "line": imp["line"], "quote": imp["quote"]},
        **{flag: True for flag in _FLAGS if imp.get(flag)},
        **imp.get("attrs", {}),
    }
    if result["status"] in _RESOLVED:
        rule = {"rule": result["rule"]} if "rule" in result else {}
        return {**base, **rule, **({"target_build_constraints": result["target_build_constraints"]} if "target_build_constraints" in result else {}), "to": _file_id(result["to"]), "names": result["names"]}
    candidates = {"candidates": result["candidates"]} if "candidates" in result else {}
    return {**base, **candidates, "to": None, "spec": result["spec"], "reason": result["reason"]}


def _resolve_all(analyzed: List[dict], extracted: Dict[str, dict], contexts: dict) -> Tuple[List[dict], List[dict]]:
    edges: List[dict] = []
    externals: List[dict] = []
    for entry in analyzed:
        path = entry["path"]
        if path not in extracted:
            continue
        adapter = adapter_for(entry["lang"])
        for imp in extracted[path]["imports"]:
            for result in adapter.resolve(imp, path, contexts[entry["lang"]]):
                if result["status"] == "external":
                    externals.append({"from": _file_id(path), "package": result["package"], "line": imp["line"]})
                else:
                    edges.append(_edge(path, imp, result))
    return edges, externals


def _edge_key(edge: dict) -> tuple:
    return (edge["from"], edge["evidence"]["line"], edge["to"] or "", edge.get("spec", ""))


def _config_references(inventory: dict, extracted: Dict[str, dict]) -> Tuple[List[dict], List[dict]]:
    """REFERENCES edges for paths a known configuration schema writes, with `via`
    naming the schema and key, plus the remote sources those files name."""
    with_refs = [path for path in sorted(extracted) if extracted[path].get("path_refs") or extracted[path].get("config_externals")]
    if not with_refs:
        return [], []
    index = pathref.build_index((entry["path"] for entry in inventory["files"]), extracted)
    edges: Dict[tuple, dict] = {}
    externals: List[dict] = []
    conventions: Dict[str, dict] = {}  # matched file -> first loader reference naming it
    for path in with_refs:
        for ref in extracted[path].get("path_refs", []):
            results = pathref.resolve_all(ref, index)
            if ref["target"] == "name" and len(results) > 1:
                # A loader that builds `<folder>/<name>` reads every such file: one edge
                # with the pattern for the map, and each file linked to its own code below.
                for result in results:
                    conventions.setdefault(result["to"], {"loader": path, "ref": ref})
                results = [{**results[0], "glob": "**/" + ref["spec"].lstrip("/")}]
            for result in results:
                if ref.get("quiet") and result["status"] not in _RESOLVED:
                    continue
                base = {"kind": "REFERENCES", "from": _file_id(path), "origin": "script", "status": result["status"],
                        "via": ref["via"], "evidence": {"path": path, "line": ref["line"], "quote": ref["quote"]}}
                glob = {key: result[key] for key in ("glob", "matched") if key in result}
                if result["status"] in _RESOLVED:
                    edge = {**base, **glob, "rule": result["rule"], "to": _file_id(result["to"])}
                else:
                    edge = {**base, **glob, "to": None, "spec": result["spec"], "reason": result["reason"]}
                if edge.get("to") != base["from"]:
                    edges.setdefault(_edge_key(edge), edge)
        externals.extend({"from": _file_id(path), "package": item["package"], "line": item["line"]}
                         for item in extracted[path].get("config_externals", []))
    for target, found in sorted(conventions.items()):
        ref = found["ref"]
        siblings = [sibling for sibling in index.children.get(posixpath.dirname(target), [])
                    if sibling.endswith(".py") and sibling in index.analyzed and sibling != found["loader"]]
        for sibling in siblings:
            edge = {"kind": "REFERENCES", "from": _file_id(sibling), "origin": "script", "status": "inferred",
                    "via": ref["via"], "rule": "same_folder_name", "to": _file_id(target), "loader": found["loader"],
                    "evidence": {"path": found["loader"], "line": ref["line"], "quote": ref["quote"]}}
            edges.setdefault((edge["from"], edge["to"], "same_folder_name"), edge)
    return list(edges.values()), externals


def _dbt_edges(inventory: dict, extracted: Dict[str, dict]) -> Tuple[List[dict], List[dict]]:
    """READS edges for `{{ ref() }}` in SQL, REFERENCES edges for model names in property
    files, and refs to packages that are not in the repository."""
    with_refs = [path for path in sorted(extracted) if extracted[path].get("dbt_refs") or extracted[path].get("dbt_names")]
    if not with_refs:
        return [], []
    projects = dbt.build((entry["path"] for entry in inventory["files"]), extracted)
    edges: Dict[tuple, dict] = {}
    externals: List[dict] = []
    for path in with_refs:
        for ref in extracted[path].get("dbt_refs", []):
            result = dbt.resolve_ref(ref, path, projects, extracted)
            if result["status"] == "external":
                externals.append({"from": _file_id(path), "package": result["package"], "line": ref["line"]})
                continue
            item = {"table": ref["ref"], "line": ref["line"], "quote": ref["quote"]}
            edge = {**_read_edge(path, item, result), "via": "dbt:ref"}
            if edge["to"] != _file_id(path):
                edges.setdefault((path, ref["line"], edge["to"] or "", ref["ref"]), edge)
        for item in extracted[path].get("dbt_names", []):
            result = dbt.resolve_name(item, path, projects, extracted)
            if result is None:
                continue
            base = {"kind": "REFERENCES", "from": _file_id(path), "origin": "script", "status": result["status"],
                    "via": f"dbt:{item['kind']}.name", "evidence": {"path": path, "line": item["line"], "quote": item["quote"]}}
            if result["status"] in _RESOLVED:
                edge = {**base, "rule": result["rule"], "to": _file_id(result["to"])}
            else:
                candidates = {"candidates": result["candidates"]} if "candidates" in result else {}
                edge = {**base, **candidates, "to": None, "spec": result["spec"], "reason": result["reason"]}
            edges.setdefault((path, item["line"], edge["to"] or "", item["name"]), edge)
    return list(edges.values()), externals


def _references(analyzed: List[dict], extracted: Dict[str, dict]) -> List[dict]:
    """REFERENCES edges: a SQL file path written as text, resolved against the parsed SQL files."""
    by_name = fileref.index(entry["path"] for entry in analyzed if entry["lang"] == "sql" and entry["path"] in extracted)
    edges: Dict[tuple, dict] = {}
    for path in sorted(extracted):
        for ref in extracted[path].get("file_refs", []):
            result = fileref.resolve(ref["spec"], path, by_name, ref["dynamic"])
            base = {
                "kind": "REFERENCES",
                "from": _file_id(path),
                "origin": "script",
                "status": result["status"],
                "evidence": {"path": path, "line": ref["line"], "quote": ref["quote"]},
            }
            if result["status"] in _RESOLVED:
                edge = {**base, "rule": result["rule"], "to": _file_id(result["to"])}
            else:
                candidates = {"candidates": result["candidates"]} if "candidates" in result else {}
                edge = {**base, **candidates, "to": None, "spec": result["spec"], "reason": result["reason"]}
            edges.setdefault(_edge_key(edge), edge)
    return list(edges.values())


def _written_tables(paths: List[str], extracted: Dict[str, dict]) -> dict:
    """Tables each SQL file writes for others to read: no temporary and no run-time names."""
    return lineage.index(
        (path, item["table"]) for path in paths for item in extracted[path]["tables"]
        if item["access"] == "write" and not item["temp"] and not item["dynamic"])


def _written_table_list(analyzed: List[dict], extracted: Dict[str, dict]) -> List[dict]:
    """Every table a SQL file writes for others to read, at its first line in that file."""
    first: Dict[tuple, int] = {}
    for entry in analyzed:
        for item in extracted.get(entry["path"], {}).get("tables", []):
            if item["access"] == "write" and not item["temp"] and not item["dynamic"]:
                first.setdefault((item["table"], entry["path"]), item["line"])
    return [{"table": table, "from": _file_id(path), "line": line} for (table, path), line in sorted(first.items())]


def _reads(analyzed: List[dict], extracted: Dict[str, dict]) -> Tuple[List[dict], List[dict]]:
    """(READS edges from a file to the files writing a table it reads, tables nothing scanned writes)."""
    paths = [entry["path"] for entry in analyzed if "tables" in extracted.get(entry["path"], {})]
    writers = _written_tables(paths, extracted)
    edges: Dict[tuple, dict] = {}
    external: Dict[tuple, dict] = {}
    for path in paths:
        records = extracted[path]["tables"]
        local = {item["table"] for item in records if item["temp"]}
        own_writes = {item["table"]: item["line"] for item in records
                      if item["access"] == "write" and not item["temp"] and not item["dynamic"]}
        for item in records:
            if item["access"] != "read" or item["table"] in local:
                continue
            if item["table"] in own_writes and own_writes[item["table"]] <= item["line"]:
                continue  # the file made this table itself before reading it
            for result in lineage.resolve(item["table"], item["dynamic"], writers):
                if result["status"] == "external":
                    external.setdefault((path, item["table"]),
                                        {"from": _file_id(path), "table": item["table"], "line": item["line"]})
                elif result.get("to") != path:
                    edge = _read_edge(path, item, result)
                    edges[(path, item["line"], edge["to"] or "", item["table"])] = edge
    return list(edges.values()), sorted(external.values(), key=lambda x: (x["from"], x["line"], x["table"]))


def _read_edge(path: str, item: dict, result: dict) -> dict:
    base = {
        "kind": "READS",
        "from": _file_id(path),
        "origin": "script",
        "status": result["status"],
        "table": item["table"],
        "evidence": {"path": path, "line": item["line"], "quote": item["quote"]},
    }
    if result["status"] in _RESOLVED:
        return {**base, "rule": result["rule"], "to": _file_id(result["to"])}
    candidates = {"candidates": result["candidates"]} if "candidates" in result else {}
    return {**base, **candidates, "to": None, "spec": result["spec"], "reason": result["reason"]}


def _dedupe_externals(externals: List[dict]) -> List[dict]:
    """One entry per (file, package), keeping the first line it appears on."""
    first: Dict[tuple, dict] = {}
    for item in sorted(externals, key=lambda x: (x["from"], x["package"], x["line"])):
        first.setdefault((item["from"], item["package"]), item)
    return sorted(first.values(), key=lambda x: (x["from"], x["line"], x["package"]))


def _nodes(analyzed: List[dict], extracted: Dict[str, dict]) -> List[dict]:
    nodes: List[dict] = []
    for entry in analyzed:
        path = entry["path"]
        if path not in extracted:
            continue
        degraded = {"degraded": True} if extracted[path].get("degraded") else {}
        nodes.append(
            {"id": _file_id(path), "type": "file", "lang": entry["lang"],
             "lines": entry["lines"], "sha256": entry["sha256"], **degraded,
             **({"build_constraints": extracted[path]["build_constraints"]} if "build_constraints" in extracted[path] else {})}
        )
        nodes.extend(
            {"id": f"sym:{path}#{symbol['name']}", "type": "symbol", "kind": symbol["kind"],
             "line": symbol["line"], "exported": symbol["exported"], "origin": "script"}
            for symbol in extracted[path]["symbols"]
        )
    return sorted(nodes, key=lambda node: node["id"])


def _skipped(inventory: dict, failures: List[str]) -> List[dict]:
    from_scan = [
        {"path": entry["path"], "reason": entry["reason"]}
        for entry in inventory["files"]
        if entry["status"] == "skipped"
    ]
    from_parse = [{"path": path, "reason": "parse_error"} for path in failures]
    return sorted(from_scan + from_parse, key=lambda item: item["path"])


_TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|specs?|e2e)(/|$)|\.(test|spec)\.[^/.]+$|(^|/)test_[^/]*$|_test\.[^/.]+$")
_UNKNOWN = None  # an effective prefix that could not be read
_MOUNT_PASSES = 8  # nested mounts: app -> api -> users, with room to spare


def _imported_names(edges: List[dict]) -> Dict[str, Dict[str, Optional[Tuple[str, str]]]]:
    """File id -> local name -> (target path, original exported name).

    A local name brought in by two different imports maps to None.
    """
    imported: Dict[str, Dict[str, Optional[Tuple[str, str]]]] = defaultdict(dict)
    for edge in edges:
        if edge["status"] not in _RESOLVED:
            continue
        aliases = edge.get("aliases", {})
        pairs = list(aliases.items()) + [(name, name) for name in edge.get("names", ())
                                         if name not in aliases.values() and name not in ("default", "*")]
        target = edge["to"][len("file:"):]
        for local, original in pairs:
            known = imported[edge["from"]]
            if local in known and known[local] != (target, original):
                known[local] = None
            else:
                known[local] = (target, original)
    return imported


def _mount_targets(extracted: Dict[str, dict], edges: List[dict]) -> Tuple[Dict[tuple, List[dict]], set]:
    """(router key -> mounts made on it from other files, router keys left unknown).

    A mount `{file, prefix, name, router}` reaches router R of file B only when
    `name` is imported from B and its original name is R, or is `default` and
    B's default export is router R. A default import of a file whose default
    export is not one of its routers leaves all of them unknown.
    """
    imported = _imported_names(edges)
    modules: Dict[str, set] = defaultdict(set)
    for edge in edges:
        if edge["status"] in _RESOLVED and not edge.get("names"):
            modules[edge["from"]].add(edge["to"][len("file:"):])
    targets: Dict[tuple, List[dict]] = defaultdict(list)
    ambiguous = set()
    for path, result in extracted.items():
        for mount in result.get("http_mounts", []):
            found = imported[_file_id(path)].get(mount["name"])
            if found is None and "attr" in mount:
                # A whole module imported under its own name (`from package import users`) leaves no imported name.
                stems = {posixpath.dirname(target) if posixpath.basename(target) == "__init__.py" else target.rsplit(".", 1)[0]
                         for target in modules[_file_id(path)]}
                matches = [target for target in modules[_file_id(path)]
                           if posixpath.basename(posixpath.dirname(target) if posixpath.basename(target) == "__init__.py"
                                                 else target.rsplit(".", 1)[0]) == mount["name"]]
                found = (matches[0], "*") if len(matches) == 1 and stems else None
            if found is None:
                continue
            target, original = found
            owned = extracted.get(target, {}).get("http_routers", {})
            if "attr" in mount:  # `module.router`: the import names the module, the attribute names its router
                if posixpath.basename(target) == "__init__.py" and mount["attr"] not in owned:
                    # `from package import module` resolves to the package; the module is its neighbour.
                    base = posixpath.join(posixpath.dirname(target), mount["name"])
                    target = next((path for path in (base + ".py", base + "/__init__.py") if path in extracted), target)
                    owned = extracted.get(target, {}).get("http_routers", {})
                if mount["attr"] in owned:
                    targets[(target, mount["attr"])].append(mount)
            elif original in owned:
                targets[(target, original)].append(mount)
            elif original == "default":
                exported = [name for name, info in owned.items() if info.get("default")]
                if len(exported) == 1:
                    targets[(target, exported[0])].append(mount)
                else:
                    ambiguous.update((target, name) for name in owned)
    return targets, ambiguous


def _effective_prefixes(extracted: Dict[str, dict], edges: List[dict]) -> Dict[tuple, Optional[str]]:
    """(file, router) -> prefix every route on that router takes, or None when unknown.

    A root router no other file mounts has the empty prefix; a sub-router no
    other file mounts is unknown. A router mounted from several places with
    different prefixes, or through a router whose own prefix is unknown, is
    unknown. Mounts that do not settle within a few passes stay unknown.
    """
    targets, ambiguous = _mount_targets(extracted, edges)
    base: Dict[tuple, Optional[str]] = {
        (path, name): (_UNKNOWN if info.get("sub") else "")
        for path, result in extracted.items()
        for name, info in result.get("http_routers", {}).items()}
    state = dict(base)
    for key in ambiguous:
        state[key] = _UNKNOWN
    settled = False
    for _ in range(_MOUNT_PASSES):
        following = dict(state)
        for key, mounts in targets.items():
            if key in ambiguous:
                continue
            values = set()
            for mount in mounts:
                outer = state.get((mount["file"], mount["router"]), _UNKNOWN)
                unreadable = mount.get("prefix_unknown") or outer is _UNKNOWN
                values.add(_UNKNOWN if unreadable else join_path(outer, mount["prefix"]))
            following[key] = values.pop() if len(values) == 1 else _UNKNOWN
        if following == state:
            settled = True
            break
        state = following
    if not settled:
        for key in targets:
            state[key] = _UNKNOWN
    return state


def _mounted_routes(extracted: Dict[str, dict], edges: List[dict]) -> List[dict]:
    """All routes, with prefixes from routers mounted in another file applied.

    A route whose router prefix could not be read carries `prefix_unknown`.
    """
    prefixes = _effective_prefixes(extracted, edges)
    routes = []
    for path, result in extracted.items():
        for found in result.get("http_routes", []):
            route = {key: value for key, value in found.items()
                     if key not in ("router", "prefix_unknown")}
            if "router" not in found:
                routes.append({**route, **({"prefix_unknown": True} if found.get("prefix_unknown") else {})})
                continue
            prefix = prefixes.get((path, found["router"]), _UNKNOWN)
            if prefix is _UNKNOWN:
                routes.append({**route, "prefix_unknown": True})
            else:
                routes.append({**route, "path": join_path(prefix, route["path"]) if prefix else route["path"]})
    return routes


def _http(extracted: Dict[str, dict], edges: List[dict]) -> Tuple[List[dict], List[dict]]:
    """(request-to-route connections, every route that was read)."""
    requests = [request for result in extracted.values() for request in result.get("http_requests", [])]
    # A call on an imported name is a request only when that name is an axios instance where it is exported.
    imported = _imported_names(edges)
    for path, result in extracted.items():
        for pending in result.get("http_pending_requests", []):
            found = imported[_file_id(path)].get(pending["client"])
            if not found:
                continue
            clients = extracted.get(found[0], {}).get("http_clients", {})
            defaults = [name for name, info in clients.items() if info.get("default")]
            if found[1] in clients or (found[1] == "default" and len(defaults) == 1):
                requests.append(pending["request"])
    requests.sort(key=lambda request: (request["file"], request["line"]))
    routes = _mounted_routes(extracted, edges)
    listed = sorted(routes, key=lambda route: (route["file"], route["line"], route["method"], route["path"]))
    # A route set up inside a test stands in for the real one; application requests are matched
    # against application routes, and only requests made by tests may reach test routes.
    live = [route for route in routes if not _TEST_FILE.search(route["file"])]
    by_tests = [request for request in requests if _TEST_FILE.search(request["file"])]
    by_app = [request for request in requests if not _TEST_FILE.search(request["file"])]
    connections = connect(by_app, live) + connect(by_tests, routes)
    connections.sort(key=lambda item: (item["request"]["file"], item["request"]["line"]))
    return connections, listed


def build_graph(inventory: dict, read_path: ReadPath, options: Optional[Dict[str, list]] = None) -> dict:
    """Deterministic: the same inventory, file contents and options give identical output.

    `options` holds run-time settings an adapter cannot see in the files, such as
    {"jpath": [...]} for Jsonnet. Given options are recorded in the graph."""
    options = {name: list(value) for name, value in (options or {}).items() if value}
    analyzed = sorted(
        (entry for entry in inventory["files"] if entry["status"] == "analyzed"),
        key=lambda entry: entry["path"],
    )
    extracted, failures = _extract_all(analyzed, read_path)
    contexts = _contexts(analyzed, extracted, inventory, read_path, options)
    edges, externals = _resolve_all(analyzed, extracted, contexts)
    repo = inventory["repo"]
    connections, routes = _http(extracted, edges)
    reads, external_tables = _reads(analyzed, extracted)
    config_edges, config_externals = _config_references(inventory, extracted)
    dbt_edges, dbt_externals = _dbt_edges(inventory, extracted)
    return {
        "schema_version": SCHEMA_VERSION,
        "inventory_sha256": inventory_digest(inventory),
        "repo": {"name": repo["name"], "commit": repo["commit"], "dirty": repo["dirty"]},
        "nodes": _nodes(analyzed, extracted),
        "edges": sorted(edges + _references(analyzed, extracted) + config_edges + reads + dbt_edges,
                       key=lambda edge: _edge_key(edge) + (edge.get("table", ""),)),
        "externals": _dedupe_externals(externals + config_externals + dbt_externals),
        "external_tables": external_tables,
        "written_tables": _written_table_list(analyzed, extracted),
        "http_connections": connections,
        "http_routes": routes,
        "skipped": _skipped(inventory, failures),
        **({"options": options} if options else {}),
    }
