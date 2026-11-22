"""FastAPI and Flask routes read from decorators, with same-file prefixes.

Routes on an `APIRouter` or `Blueprint` carry `prefix_unknown` unless this file
includes that router exactly once on a router whose own prefix is known.
"""
from __future__ import annotations

import ast
from typing import Dict, List, Optional, Tuple

from ..httpmatch import join_path

_ROOTS = {"FastAPI", "Flask"}
_SUBS = {"APIRouter", "Blueprint"}
_VERBS = {"get", "post", "put", "patch", "delete", "options", "head"}
_PREFIX_KEYS = ("prefix", "url_prefix")
_INCLUDES = {"include_router": "router", "register_blueprint": "blueprint"}
_UNREADABLE = object()


def _text(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _prefix(call):
    """The literal prefix keyword, "" without one, or _UNREADABLE."""
    node = next((k.value for k in call.keywords if k.arg in _PREFIX_KEYS), None)
    if node is None:
        return ""
    value = _text(node)
    return _UNREADABLE if value is None else value


def _callee(call):
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


def _bindings(tree) -> Dict[str, dict]:
    """Router name -> {"root": bool, "prefix": own prefix or _UNREADABLE}."""
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not (isinstance(value, ast.Call) and _callee(value) in _ROOTS | _SUBS):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = {"root": _callee(value) in _ROOTS, "prefix": _prefix(value)}
    return found


def _mount_arguments(call):
    """(prefix node, mounted node) of a `.mount(prefix, app)` call."""
    def pick(position, keyword):
        if len(call.args) > position:
            return call.args[position]
        return next((k.value for k in call.keywords if k.arg == keyword), None)
    return pick(0, "path"), pick(1, "app")


def _includes(tree, known) -> Dict[str, List[tuple]]:
    """Router name -> [(receiving name or None, prefix, replaces)] per include call.

    `replaces` is true for a Flask blueprint registered with its own
    `url_prefix`, which stands in for the blueprint's prefix instead of
    extending it. A root object passed to `.mount(prefix, obj)` is included
    with that prefix; only a root receiver gives it a known prefix.
    """
    found: Dict[str, List[tuple]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        receiver = node.func.value
        name = receiver.id if isinstance(receiver, ast.Name) and receiver.id in known else None
        if node.func.attr == "mount":
            path, included = _mount_arguments(node)
            if not (isinstance(included, ast.Name) and known.get(included.id, {}).get("root")):
                continue
            prefix = _text(path)
            if name is not None and not known[name]["root"]:
                name = None
            found.setdefault(included.id, []).append(
                (name, _UNREADABLE if prefix is None else prefix, False))
            continue
        if node.func.attr not in _INCLUDES:
            continue
        keyword = _INCLUDES[node.func.attr]
        included = node.args[0] if node.args else next(
            (k.value for k in node.keywords if k.arg == keyword), None)
        if not (isinstance(included, ast.Name) and included.id in known):
            continue
        replaces = node.func.attr == "register_blueprint" and any(
            k.arg == "url_prefix" for k in node.keywords)
        found.setdefault(included.id, []).append((name, _prefix(node), replaces))
    return found


def _prefixes(tree) -> Dict[str, Tuple[str, bool]]:
    """Router name -> (same-file prefix, whether the whole prefix is known).

    An unknown prefix still carries the router's own literal prefix, so the
    listed path stays close to what the source says.
    """
    known = _bindings(tree)
    includes = _includes(tree, known)

    def resolve(name, seen):
        own = known[name]["prefix"]
        if own is _UNREADABLE:
            return "", False
        calls = includes.get(name, [])
        if name in seen or len(calls) > 1:
            return own, False
        if not calls:
            return own, known[name]["root"]
        receiver, prefix, replaces = calls[0]
        if receiver is None or prefix is _UNREADABLE:
            return own, False
        outer, outer_known = resolve(receiver, seen | {name})
        if not outer_known:
            return own, False
        if replaces:
            return join_path(outer, prefix) if prefix else outer, True
        return join_path(join_path(outer, prefix), own) if own else join_path(outer, prefix), True

    return {name: resolve(name, frozenset()) for name in known}


def _methods(decorator) -> Optional[List[str]]:
    verb = decorator.func.attr
    if verb in _VERBS:
        return [verb.upper()]
    if verb != "route":
        return None
    listed = next((k.value for k in decorator.keywords if k.arg == "methods"), None)
    if listed is None:
        return ["GET"]
    if not isinstance(listed, (ast.List, ast.Tuple, ast.Set)):
        return None
    names = [_text(item) for item in listed.elts]
    if not names or any(name is None for name in names):
        return None
    return sorted({name.upper() for name in names})


def _mounts(path: str, tree, known, prefixes) -> list:
    """Includes of a router this file imports: `app.include_router(users.router, prefix="/users")`.

    The graph step follows the import to the file that owns the router. A Flask
    `url_prefix` replaces the blueprint's own prefix, which only that file knows, so
    such a mount is recorded as unreadable.
    """
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _INCLUDES
                and isinstance(node.func.value, ast.Name) and node.func.value.id in known):
            continue
        keyword = _INCLUDES[node.func.attr]
        included = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == keyword), None)
        if isinstance(included, ast.Name) and included.id not in known:
            target = {"name": included.id}
        elif isinstance(included, ast.Attribute) and isinstance(included.value, ast.Name) and included.value.id not in known:
            target = {"name": included.value.id, "attr": included.attr}
        else:
            continue
        receiver = node.func.value.id
        prefix = _prefix(node)
        own = known[receiver]["prefix"]
        replaces = node.func.attr == "register_blueprint" and any(k.arg == "url_prefix" for k in node.keywords)
        unreadable = prefix is _UNREADABLE or own is _UNREADABLE or replaces
        found.append({"file": path, "router": receiver, **target,
                      "prefix": "" if unreadable else (join_path(own, prefix) if prefix else own),
                      **({"prefix_unknown": True} if unreadable else {})})
    return found


def routes(path: str, text: str) -> list:
    """Routes as this file alone shows them: a router awaiting a mount elsewhere has an unknown prefix."""
    return [{**{key: value for key, value in route.items() if key != "router"}, **({"prefix_unknown": True} if "router" in route else {})}
            for route in routes_and_mounts(path, text)[0]]


def routes_and_mounts(path: str, text: str):
    """(routes, mounts of imported routers, routers declared here) in the shape the graph step joins."""
    try:
        tree = ast.parse(text)
        prefixes = _prefixes(tree)
    except (SyntaxError, ValueError, RecursionError):
        return [], [], {}
    known = _bindings(tree)
    included_here = _includes(tree, known)
    routers = {name: {"sub": not info["root"]} for name, info in known.items()}
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id in prefixes and decorator.args):
                continue
            route, methods = _text(decorator.args[0]), _methods(decorator)
            if route is None or not route.startswith("/") or methods is None:
                continue
            name = decorator.func.value.id
            prefix, settled = prefixes[name]
            flag = {} if settled else {"prefix_unknown": True}
            # A sub-router no call in this file includes may be mounted from another file.
            if not settled and not included_here.get(name) and known[name]["prefix"] is not _UNREADABLE:
                flag = {"router": name}
            full = join_path(prefix, route)
            for method in methods:
                found.append({"file": path, "line": decorator.lineno, "handler_line": node.lineno,
                              "method": method, "path": full, "dynamic": False, **flag})
    return sorted(found, key=lambda item: (item["line"], item["method"])), _mounts(path, tree, known, prefixes), routers
