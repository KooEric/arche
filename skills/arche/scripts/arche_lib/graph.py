"""Build graph.json content: file and symbol nodes plus IMPORTS edges."""
from __future__ import annotations

from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple

from . import SCHEMA_VERSION
from .errors import ParseError
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


def _contexts(analyzed: List[dict], extracted: Dict[str, dict], inventory: dict, read_path: ReadPath) -> dict:
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
        by_adapter[adapter.__name__] = adapter.build_context(parsed, all_paths, read_path)
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


def _http_connections(extracted: Dict[str, dict]) -> List[dict]:
    """Link literal Dart requests to registered routes by verb and path."""
    routes = defaultdict(list)
    for result in extracted.values():
        for route in result.get("http_routes", []):
            if not route["dynamic"]:
                routes[route["method"], route["path"]].append(route)

    connections = []
    for result in extracted.values():
        for request in result.get("http_requests", []):
            candidates = [] if request["dynamic"] else routes[request["method"], request["path"]]
            if request["dynamic"]:
                reason = "dynamic_request_path"
            elif len(candidates) > 1:
                reason = "multiple_handlers"
            elif candidates:
                reason = "method_and_path"
            else:
                reason = "no_literal_handler"
            connections.append({
                "request": request, "candidates": candidates, "reason": reason,
                "status": "inferred" if len(candidates) == 1 else "unresolved",
            })
    return connections


def build_graph(inventory: dict, read_path: ReadPath) -> dict:
    """Deterministic: the same inventory and file contents give identical output."""
    analyzed = sorted(
        (entry for entry in inventory["files"] if entry["status"] == "analyzed"),
        key=lambda entry: entry["path"],
    )
    extracted, failures = _extract_all(analyzed, read_path)
    contexts = _contexts(analyzed, extracted, inventory, read_path)
    edges, externals = _resolve_all(analyzed, extracted, contexts)
    repo = inventory["repo"]
    return {
        "schema_version": SCHEMA_VERSION,
        "repo": {"name": repo["name"], "commit": repo["commit"], "dirty": repo["dirty"]},
        "nodes": _nodes(analyzed, extracted),
        "edges": sorted(edges, key=_edge_key),
        "externals": _dedupe_externals(externals),
        "http_connections": _http_connections(extracted),
        "skipped": _skipped(inventory, failures),
    }
