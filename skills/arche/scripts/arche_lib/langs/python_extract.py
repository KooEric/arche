"""Python imports and top-level symbols, read with the built-in ast module."""
from __future__ import annotations

import ast
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple

from ..errors import ParseError

MAX_QUOTE_CHARS = 200
_DYNAMIC_FUNCTIONS = frozenset({"import_module", "__import__"})
_BRANCH_NODE_NAMES = (
    "If", "Try", "TryStar", "With", "AsyncWith", "For", "AsyncFor", "While",
    "Match", "FunctionDef", "AsyncFunctionDef", "ClassDef",
)
_BRANCH_NODES = tuple(getattr(ast, name) for name in _BRANCH_NODE_NAMES if hasattr(ast, name))


def extract(path: str, text: str) -> dict:
    """Return {"imports": [...], "symbols": [...]} or raise ParseError."""
    try:
        tree = ast.parse(text)
        lines = text.splitlines()
        imports = list(_walk(tree.body, lines, False, False))
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ParseError(f"{path}: {exc}") from None
    return {"imports": imports, "symbols": _top_level_symbols(tree)}


def _quote(lines: Sequence[str], lineno: int) -> str:
    if not 1 <= lineno <= len(lines):
        return ""
    return lines[lineno - 1].strip()[:MAX_QUOTE_CHARS]


def _record(kind, module, level, names, node, lines, conditional, type_only, dynamic=False) -> dict:
    return {
        "kind": kind,
        "module": module,
        "level": level,
        "names": list(names),
        "line": node.lineno,
        "quote": _quote(lines, node.lineno),
        "conditional": conditional,
        "type_only": type_only,
        "dynamic": dynamic,
    }


def _is_type_checking(test: ast.AST) -> bool:
    name = getattr(test, "id", None) or getattr(test, "attr", None)
    return name == "TYPE_CHECKING"


def _dynamic_module(call: ast.Call) -> Tuple[bool, Optional[str]]:
    """(is a dynamic import call, literal module name or None)."""
    func = call.func
    name = getattr(func, "id", None) or getattr(func, "attr", None)
    if name not in _DYNAMIC_FUNCTIONS:
        return False, None
    first = call.args[0] if call.args else None
    literal = first.value if isinstance(first, ast.Constant) else None
    if isinstance(literal, str) and literal and not literal.startswith("."):
        return True, literal
    return True, None


def _walk(nodes: Iterable[ast.AST], lines, conditional: bool, type_only: bool) -> Iterator[dict]:
    """Visit every node in source order. An explicit stack keeps large files fast."""
    stack = [(node, conditional, type_only) for node in reversed(list(nodes))]
    while stack:
        node, conditional, type_only = stack.pop()
        kind = node.__class__
        if kind is ast.Name or kind is ast.Constant:
            continue
        if kind is ast.Import:
            for alias in node.names:
                record = _record("import", alias.name, 0, [], node, lines, conditional, type_only)
                if alias.asname:
                    record["attrs"] = {"aliases": {alias.asname: "*"}}  # the module itself under a local name
                yield record
            continue
        if kind is ast.ImportFrom:
            names = [alias.name for alias in node.names]
            record = _record("from", node.module or "", node.level, names, node, lines, conditional, type_only)
            renamed = {alias.asname: alias.name for alias in node.names if alias.asname}
            if renamed:
                record["attrs"] = {"aliases": renamed}
            yield record
            continue
        if kind is ast.If and _is_type_checking(node.test):
            stack.extend((child, True, type_only) for child in reversed(node.orelse))
            stack.extend((child, True, True) for child in reversed(node.body))
            continue
        if kind is ast.Call:
            is_dynamic, module = _dynamic_module(node)
            if is_dynamic:
                yield _record("dynamic", module, 0, [], node, lines, conditional, type_only, dynamic=True)
        nested = conditional or isinstance(node, _BRANCH_NODES)
        children = []
        for field in node._fields:
            value = getattr(node, field, None)
            if value.__class__ is list:
                children.extend(item for item in value if isinstance(item, ast.AST))
            elif isinstance(value, ast.AST):
                children.append(value)
        stack.extend((child, nested, type_only) for child in reversed(children))


def _definitions(node: ast.AST) -> List[Tuple[str, str]]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [(node.name, "function")]
    if isinstance(node, ast.ClassDef):
        return [(node.name, "class")]
    if isinstance(node, ast.Assign):
        return [(target.id, "variable") for target in node.targets if isinstance(target, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [(node.target.id, "variable")]
    return []


def _top_level_symbols(tree: ast.Module) -> List[dict]:
    seen = set()
    symbols = []
    for node in tree.body:
        for name, kind in _definitions(node):
            if name in seen:
                continue
            seen.add(name)
            symbols.append(
                {"name": name, "kind": kind, "line": node.lineno, "exported": not name.startswith("_")}
            )
    return symbols
