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


def _includes(tree, known) -> Dict[str, List[tuple]]:
    """Router name -> [(receiving router name or None, include prefix)] per include call."""
    found: Dict[str, List[tuple]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _callee(node) in _INCLUDES):
            continue
        keyword = _INCLUDES[_callee(node)]
        included = node.args[0] if node.args else next(
            (k.value for k in node.keywords if k.arg == keyword), None)
        if not (isinstance(included, ast.Name) and included.id in known):
            continue
        receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
        name = receiver.id if isinstance(receiver, ast.Name) and receiver.id in known else None
        found.setdefault(included.id, []).append((name, _prefix(node)))
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
        receiver, prefix = calls[0]
        if receiver is None or prefix is _UNREADABLE:
            return own, False
        outer, outer_known = resolve(receiver, seen | {name})
        if not outer_known:
            return own, False
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


def routes(path: str, text: str) -> list:
    try:
        tree = ast.parse(text)
        prefixes = _prefixes(tree)
    except (SyntaxError, ValueError, RecursionError):
        return []
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
            prefix, known = prefixes[decorator.func.value.id]
            flag = {} if known else {"prefix_unknown": True}
            full = join_path(prefix, route)
            for method in methods:
                found.append({"file": path, "line": decorator.lineno, "handler_line": node.lineno,
                              "method": method, "path": full, "dynamic": False, **flag})
    return sorted(found, key=lambda item: (item["line"], item["method"]))
