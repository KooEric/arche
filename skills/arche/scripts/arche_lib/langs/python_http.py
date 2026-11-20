"""FastAPI and Flask routes read from decorators, with same-file prefixes."""
from __future__ import annotations

import ast

from ..httpmatch import join_path

_FACTORIES = {"FastAPI", "APIRouter", "Flask", "Blueprint"}
_VERBS = {"get", "post", "put", "patch", "delete", "options", "head"}
_PREFIX_KEYS = ("prefix", "url_prefix")


def _text(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _keyword(call, names):
    return next((_text(k.value) for k in call.keywords if k.arg in names), None)


def _callee(call):
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


def routes(path: str, text: str) -> list:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return []
    prefixes = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and _callee(node.value) in _FACTORIES):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    prefixes[target.id] = _keyword(node.value, _PREFIX_KEYS) or ""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and _callee(node) in ("include_router", "register_blueprint")
                and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in prefixes):
            prefixes[node.args[0].id] = (_keyword(node, _PREFIX_KEYS) or "") + prefixes[node.args[0].id]
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id in prefixes and decorator.args):
                continue
            verb, route = decorator.func.attr, _text(decorator.args[0])
            if route is None or not route.startswith("/"):
                continue
            if verb in _VERBS:
                methods = [verb.upper()]
            elif verb == "route":
                listed = next((k.value for k in decorator.keywords if k.arg == "methods"), None)
                methods = ([_text(item).upper() for item in listed.elts if _text(item)]
                           if isinstance(listed, (ast.List, ast.Tuple)) else ["GET"])
            else:
                continue
            full = join_path(prefixes[decorator.func.value.id], route)
            for method in methods:
                found.append({"file": path, "line": decorator.lineno, "handler_line": node.lineno,
                              "method": method, "path": full, "dynamic": False})
    return sorted(found, key=lambda item: (item["line"], item["method"]))
