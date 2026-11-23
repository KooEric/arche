"""Build atlas.json: the checked map of parts, files and connections that the viewer draws.

The agent names the parts in map.json. This module assigns files to them, counts the
connections the scan found between them, and checks every cited location. Without a
map.json it falls back to folders so that a map always exists.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from fnmatch import fnmatchcase
from typing import Optional

from .errors import IntegrityError

ATLAS_VERSION = 1
KINDS = ("ui", "service", "worker", "library", "data", "test", "script", "config", "external")
AUDIENCES = ("everyone", "developers")
MAX_DEPTH = 3
MIN_TOP, MAX_TOP = 3, 9
UNASSIGNED_WARNING = 0.10
LARGE_PART_FILES = 24
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _bad(where: str, message: str, hint: str = "") -> None:
    raise IntegrityError(f"invalid map.json: {where}: {message}" + (f" (did you mean {hint}?)" if hint else ""))


def _text(value, where: str, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip():
        _bad(where, "must be a non-empty string")
    return value.strip()


def _list(value, where: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        _bad(where, "must be a list")
    return value


def _closest(name: str, options) -> str:
    """A near match by shared prefix, for the hint in an error message."""
    best = max(options, key=lambda option: (sum(1 for a, b in zip(str(option), name) if a == b), option), default="")
    return str(best)


def _evidence(value, where: str, inventory_files: dict) -> Optional[dict]:
    if value is None:
        return None
    if not isinstance(value, dict):
        _bad(where, "must be an object with path, start_line and end_line")
    path = _text(value.get("path"), f"{where}.path")
    entry = inventory_files.get(path)
    if entry is None:
        _bad(f"{where}.path", f"{path} is not in the scan", _closest(path, inventory_files))
    if entry.get("status") == "skipped":
        _bad(f"{where}.path", f"{path} was excluded from the scan ({entry.get('reason')})")
    start, end = value.get("start_line"), value.get("end_line", value.get("start_line"))
    if type(start) is not int or type(end) is not int or not 1 <= start <= end:
        _bad(where, "start_line and end_line must be positive integers in order")
    if end > entry.get("lines", 0):
        _bad(where, f"line {end} is past the end of {path} ({entry.get('lines', 0)} lines)")
    return {"path": path, "start_line": start, "end_line": end, "sha256": entry["sha256"]}


def _matches(pattern: str, path: str) -> bool:
    pattern = pattern.rstrip("/")
    if not any(char in pattern for char in "*?["):
        return path == pattern or path.startswith(pattern + "/")
    return fnmatchcase(path, pattern) or fnmatchcase(path, pattern + "/*")


def _literal_prefix(pattern: str) -> int:
    return len(re.split(r"[*?\[]", pattern, maxsplit=1)[0])


def _auto_parts(paths: list) -> list:
    """Folders, split until the top level holds a readable number of parts."""
    groups = defaultdict(list)
    for path in paths:
        groups[path.split("/", 1)[0] if "/" in path else "."].append(path)
    settled = set()  # folders already split: what stays under their name is their own files
    while len(groups) < MIN_TOP:
        splittable = [name for name, members in groups.items()
                      if name != "." and name not in settled and any("/" in member[len(name) + 1:] for member in members)]
        if not splittable:
            break
        name = max(splittable, key=lambda item: (len(groups[item]), item))
        settled.add(name)
        for member in groups.pop(name):
            rest = member[len(name) + 1:]
            groups[name + "/" + rest.split("/", 1)[0] if "/" in rest else name].append(member)
    ranked = sorted(groups, key=lambda name: (-len(groups[name]), name))
    kept = ranked if len(ranked) <= MAX_TOP else ranked[:MAX_TOP - 1]
    parts = [{"id": name.replace("/", ".") if name != "." else "root", "label": name, "kind": "library",
              "paths": [name] if name != "." else [], "root_files": name == ".", "unnamed": True}
             for name in sorted(kept)]
    if len(ranked) > len(kept):
        parts.append({"id": "other", "label": "other folders", "kind": "library", "paths": sorted(ranked[len(kept):]), "unnamed": True})
    return parts


def _read_parts(document: dict, inventory_files: dict) -> list:
    parts, seen = [], set()
    for index, raw in enumerate(_list(document.get("parts"), "parts")):
        where = f"parts[{index}]"
        if not isinstance(raw, dict):
            _bad(where, "must be an object")
        part_id = _text(raw.get("id"), f"{where}.id")
        if not _ID.fullmatch(part_id) or part_id in seen:
            _bad(f"{where}.id", f"{part_id!r} must be unique and use letters, digits, '.', '_' or '-'")
        seen.add(part_id)
        kind = raw.get("kind", "library")
        if kind not in KINDS:
            _bad(f"{where}.kind", f"{kind!r} is not one of {', '.join(KINDS)}", _closest(str(kind), KINDS))
        for key in ("paths", "packages", "hosts"):
            for item in _list(raw.get(key), f"{where}.{key}"):
                _text(item, f"{where}.{key}[]")
        parts.append({
            "id": part_id, "label": _text(raw.get("label"), f"{where}.label"), "kind": kind,
            "summary": _text(raw.get("summary"), f"{where}.summary", required=False),
            "parent": raw.get("parent"), "paths": list(raw.get("paths") or []),
            "packages": list(raw.get("packages") or []), "hosts": list(raw.get("hosts") or []),
            "evidence": _evidence(raw.get("evidence"), f"{where}.evidence", inventory_files),
        })
    if not parts:
        _bad("parts", "needs at least one part")
    by_id = {part["id"]: part for part in parts}
    for index, part in enumerate(parts):
        parent, depth = part["parent"], 1
        if parent is not None and parent not in by_id:
            _bad(f"parts[{index}].parent", f"unknown part {parent!r}", _closest(str(parent), by_id))
        while parent is not None:
            depth += 1
            if depth > MAX_DEPTH or parent == part["id"]:
                _bad(f"parts[{index}].parent", f"parts nest at most {MAX_DEPTH} levels and cannot form a loop")
            parent = by_id[parent]["parent"]
        part["depth"] = depth
    return parts


def _assign(parts: list, paths: list) -> dict:
    """Each file goes to the deepest part that claims it; ties go to the longer literal prefix."""
    owner = {}
    for path in paths:
        best = None
        for part in parts:
            if part.get("root_files") and "/" not in path:
                score = (part.get("depth", 1), 0)
            else:
                lengths = [_literal_prefix(pattern) for pattern in part["paths"] if _matches(pattern, path)]
                if not lengths:
                    continue
                score = (part.get("depth", 1), max(lengths))
            if best is None or score > best[0]:
                best = (score, part["id"])
        if best:
            owner[path] = best[1]
    return owner


def _fold_large_parts(parts: list, owner: dict) -> None:
    """Give a part with too many files of its own one card per folder, so every level stays readable.

    The folders are plain containers, not parts someone named: the viewer shows them as folders.
    """
    queue = [part["id"] for part in parts]
    while queue:
        part_id = queue.pop()
        own = sorted(path for path, holder in owner.items() if holder == part_id)
        if len(own) <= LARGE_PART_FILES:
            continue
        split = [path.split("/") for path in own]
        depth = 0
        while all(len(bits) > depth + 1 for bits in split) and len({bits[depth] for bits in split}) == 1:
            depth += 1
        groups = defaultdict(list)
        for path, bits in zip(own, split):
            if len(bits) > depth + 1:
                groups["/".join(bits[:depth + 1])].append(path)
        if len(groups) < 2 and not (groups and len(next(iter(groups.values()))) < len(own)):
            continue  # one flat folder: nothing to fold
        parent = next(part for part in parts if part["id"] == part_id)
        for folder, members in sorted(groups.items()):
            child = {"id": f"{part_id}:{folder}", "label": folder.rsplit("/", 1)[-1] + "/", "kind": parent["kind"], "summary": "",
                     "parent": part_id, "paths": [], "packages": [], "hosts": [], "evidence": None,
                     "depth": parent.get("depth", 1) + 1, "folder": True}
            parts.append(child)
            for path in members:
                owner[path] = child["id"]
            queue.append(child["id"])


def _ancestors(part_id, by_id):
    chain = []
    while part_id is not None:
        chain.append(part_id)
        part_id = by_id[part_id]["parent"]
    return chain


def build_atlas(inventory: dict, graph: dict, document: Optional[dict] = None, descriptions: Optional[dict] = None,
                changes: Optional[dict] = None, findings: Optional[list] = None) -> dict:
    """Return the atlas. Raises IntegrityError with the failing field when map.json is wrong."""
    inventory_files = {entry["path"]: entry for entry in inventory["files"]}
    files = [node for node in graph["nodes"] if node["type"] == "file"]
    paths = [node["id"][5:] for node in files]
    descriptions = descriptions or {}
    warnings = []

    if document is None:
        parts = _auto_parts(paths)
        for part in parts:
            part.update({"summary": "", "parent": None, "packages": [], "hosts": [], "evidence": None, "depth": 1})
        project = {"name": graph["repo"]["name"], "summary": ""}
        audience, flows, cited = "developers", [], []
    else:
        if not isinstance(document, dict) or document.get("version") != ATLAS_VERSION:
            _bad("version", f"must be {ATLAS_VERSION}")
        raw_project = document.get("project") if isinstance(document.get("project"), dict) else {}
        project = {"name": _text(raw_project.get("name", graph["repo"]["name"]), "project.name"),
                   "summary": _text(raw_project.get("summary"), "project.summary", required=False)}
        audience = document.get("audience", "everyone")
        if audience not in AUDIENCES:
            _bad("audience", f"{audience!r} is not one of {', '.join(AUDIENCES)}")
        parts = _read_parts(document, inventory_files)
        flows, cited = [], []
    screens = []
    by_id = {part["id"]: part for part in parts}

    owner = _assign(parts, paths)
    _fold_large_parts(parts, owner)
    by_id = {part["id"]: part for part in parts}
    unassigned = [path for path in paths if path not in owner]
    if paths and len(unassigned) / len(paths) > UNASSIGNED_WARNING:
        folders = Counter(path.rsplit("/", 1)[0] if "/" in path else "." for path in unassigned)
        listing = ", ".join(f"{folder} ({count})" for folder, count in folders.most_common(5))
        warnings.append(f"{len(unassigned)} of {len(paths)} files belong to no part: {listing}")

    if document is not None:
        for index, raw in enumerate(_list(document.get("edges"), "edges")):
            where = f"edges[{index}]"
            if not isinstance(raw, dict):
                _bad(where, "must be an object")
            for end in ("from", "to"):
                if raw.get(end) not in by_id:
                    _bad(f"{where}.{end}", f"unknown part {raw.get(end)!r}", _closest(str(raw.get(end)), by_id))
            evidence = _evidence(raw.get("evidence"), f"{where}.evidence", inventory_files)
            cited.append({"from": raw["from"], "to": raw["to"], "status": "cited", "kind": str(raw.get("kind", "cited")),
                          "count": 1, "label": _text(raw.get("label"), f"{where}.label", required=False), "evidence": evidence})
            if cited[-1]["evidence"] is None:
                warnings.append(f"{where} has no evidence; the viewer marks it as a claim without a source")
        for index, raw in enumerate(_list(document.get("screens"), "screens")):
            where = f"screens[{index}]"
            if not isinstance(raw, dict):
                _bad(where, "must be an object")
            if raw.get("part") is not None and raw["part"] not in by_id:
                _bad(f"{where}.part", f"unknown part {raw['part']!r}", _closest(str(raw["part"]), by_id))
            items = []
            for number, item in enumerate(_list(raw.get("items"), f"{where}.items")):
                spot = f"{where}.items[{number}]"
                if not isinstance(item, dict):
                    _bad(spot, "must be an object")
                items.append({"label": _text(item.get("label"), f"{spot}.label"), "behavior": _text(item.get("behavior"), f"{spot}.behavior"),
                              "evidence": _evidence(item.get("evidence"), f"{spot}.evidence", inventory_files)})
            if not items:
                _bad(f"{where}.items", "needs at least one item")
            screens.append({"title": _text(raw.get("title"), f"{where}.title"), "part": raw.get("part"), "items": items})
        for index, raw in enumerate(_list(document.get("flows"), "flows")):
            where = f"flows[{index}]"
            if not isinstance(raw, dict):
                _bad(where, "must be an object")
            steps = []
            for number, step in enumerate(_list(raw.get("steps"), f"{where}.steps")):
                spot = f"{where}.steps[{number}]"
                if not isinstance(step, dict) or step.get("part") not in by_id:
                    _bad(f"{spot}.part", "unknown part", _closest(str(isinstance(step, dict) and step.get("part")), by_id))
                steps.append({"part": step["part"], "note": _text(step.get("note"), f"{spot}.note"),
                              "evidence": _evidence(step.get("evidence"), f"{spot}.evidence", inventory_files)})
            if not steps:
                _bad(f"{where}.steps", "needs at least one step")
            flows.append({"id": _text(raw.get("id"), f"{where}.id"), "title": _text(raw.get("title"), f"{where}.title"), "steps": steps})

    # Connections the scan found, counted between the parts that own each end.
    counts = Counter()
    file_links = Counter()
    index_of = {path: position for position, path in enumerate(paths)}
    for edge in graph["edges"]:
        source, target = edge["from"][5:], (edge["to"] or "")[5:]
        if target not in index_of or source == target:
            continue
        file_links[index_of[source], index_of[target], edge["status"]] += 1
        a, b = owner.get(source), owner.get(target)
        if a and b and a != b:
            kind = "reference" if edge.get("kind") == "REFERENCES" else "import"
            counts[a, b, edge["status"], kind, None, None] += 1
    host_parts = {host: part["id"] for part in parts for host in part["hosts"]}
    for connection in graph.get("http_connections", ()):
        request = connection["request"]
        a = owner.get(request["file"])
        if connection["status"] == "inferred" and len(connection["candidates"]) == 1:
            b = owner.get(connection["candidates"][0]["file"])
            if a and b and a != b:
                counts[a, b, "inferred", "http", request["file"], connection["candidates"][0]["file"]] += 1
        elif request.get("host") in host_parts and a and a != host_parts[request["host"]]:
            counts[a, host_parts[request["host"]], "inferred", "http", request["file"], None] += 1
    package_parts = [(package, part["id"]) for part in parts for package in part["packages"]]
    for item in graph["externals"]:
        a = owner.get(item["from"][5:])
        for package, part_id in package_parts:
            if a and a != part_id and (item["package"] == package or item["package"].startswith(package + ".")
                                       or item["package"].startswith(package + "/") or item["package"].startswith(package + ":")):
                counts[a, part_id, "verified", "package", item["from"][5:], None] += 1
    # The file at each end, where known, lets the viewer keep a link when its part is opened.
    links = [{"from": a, "to": b, "status": status, "kind": kind, "count": count,
              **({"from_file": index_of[source]} if source else {}), **({"to_file": index_of[target]} if target else {})}
             for (a, b, status, kind, source, target), count in sorted(counts.items(), key=lambda kv: tuple(str(x) for x in kv[0]))]
    for link in cited:
        path = link["evidence"]["path"] if link["evidence"] else None
        if path in index_of and owner.get(path):
            ends = [end for end in ("from", "to") if link[end] in _ancestors(owner[path], by_id)]
            if ends:
                link[ends[0] + "_file"] = index_of[path]
    links += cited

    # A link between two folders also connects the parts that hold them.
    connected = set()
    for link in links:
        for a in _ancestors(link["from"], by_id):
            for b in _ancestors(link["to"], by_id):
                connected.update(((a, b), (b, a)))
    for flow in flows:
        for before, after in zip(flow["steps"], flow["steps"][1:]):
            if before["part"] != after["part"] and (before["part"], after["part"]) not in connected:
                warnings.append(f"flow {flow['id']}: no recorded connection between {before['part']} and {after['part']}")

    members = Counter(owner.values())
    children = Counter(part["parent"] for part in parts if part["parent"])
    out_parts = []
    for part in parts:
        if document is not None and not part["summary"] and not part.get("folder"):
            warnings.append(f"part {part['id']} has no summary")
        if part["kind"] != "external" and not part.get("folder") and not members[part["id"]] and not children[part["id"]]:
            warnings.append(f"part {part['id']} matches no scanned file")
        out_parts.append({key: part[key] for key in ("id", "label", "kind", "summary", "parent", "evidence")}
                         | {"unnamed": bool(part.get("unnamed")), "folder": bool(part.get("folder")), "changed": 0, "findings": 0})

    symbols = Counter(node["id"][4:].rsplit("#", 1)[0] for node in graph["nodes"] if node["type"] == "symbol")
    out_files = []
    for position, node in enumerate(files):
        path = paths[position]
        note = descriptions.get(path) if isinstance(descriptions.get(path), dict) else {}
        out_files.append({"path": path, "lang": node["lang"], "lines": node["lines"], "symbols": symbols[path],
                          "part": owner.get(path), "name": str(note.get("name") or ""), "description": str(note.get("description") or "")})
    routes = []
    for route in graph.get("http_routes", ()):
        key = f"{route['method']} {route['path']}"
        note = descriptions.get(key) if isinstance(descriptions.get(key), dict) else {}
        routes.append({"method": route["method"], "path": route["path"], "file": route["file"], "line": route["line"],
                       "name": str(note.get("name") or ""), "description": str(note.get("description") or "")})

    described_files = sum(1 for item in out_files if item["description"])
    described_routes = sum(1 for item in routes if item["description"])
    atlas = {
        "schema_version": ATLAS_VERSION,
        "repo": graph["repo"],
        "inventory_sha256": graph["inventory_sha256"],
        "generated_from": "map.json" if document is not None else "folders",
        "project": project,
        "audience": audience,
        "parts": out_parts,
        "files": out_files,
        "file_links": [[a, b, status, count] for (a, b, status), count in sorted(file_links.items())],
        "links": links,
        "routes": routes,
        "flows": flows,
        "screens": screens,
        "warnings": warnings,
        "stats": {"files": len(paths), "assigned": len(paths) - len(unassigned), "unassigned": unassigned,
                  "described_files": described_files, "routes": len(routes), "described_routes": described_routes},
    }
    return attach_review(atlas, changes, findings)


def attach_review(atlas: dict, changes: Optional[dict], findings: Optional[list]) -> dict:
    """Put the change comparison and the audit candidates on the map, per part and as lists."""
    owner = {item["path"]: item["part"] for item in atlas["files"]}
    atlas["changes"] = [{"path": item["path"], "status": item["status"], "part": owner.get(item["path"])}
                        for item in (changes or {}).get("files", ())]
    atlas["findings"] = [{"rule": item["rule"], "path": item["path"], "line": item["line"], "name": item.get("name", ""),
                          "part": owner.get(item["path"])} for item in findings or ()]
    changed = Counter(item["part"] for item in atlas["changes"])
    audited = Counter(item["part"] for item in atlas["findings"])
    for part in atlas["parts"]:
        part["changed"], part["findings"] = changed[part["id"]], audited[part["id"]]
    return atlas


def read_descriptions(out_dir) -> dict:
    """Merge descriptions/*.json. Keys are file paths or "METHOD /path"."""
    from .jsonio import read_json

    merged = {}
    folder = out_dir / "descriptions"
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        content = read_json(path)
        if not isinstance(content, dict):
            raise IntegrityError(f"invalid descriptions file: {path.name} must hold an object")
        merged.update(content)
    return merged


def load_inputs(out_dir):
    """(inventory, graph) for a run directory, checked against each other."""
    from .jsonio import check_graph, read_snapshot

    inventory = read_snapshot(out_dir / "inventory.json")
    graph = read_snapshot(out_dir / "graph.json")
    check_graph(inventory, graph)
    return inventory, graph
