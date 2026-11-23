"""SQL file paths written as text in a Python file, read with the built-in ast module."""
from __future__ import annotations

import ast
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ..fileref import is_path, is_template

MAX_QUOTE_CHARS = 200
_JOIN_NAMES = ("join", "joinpath")
Found = Optional[Tuple[str, int]]


def file_refs(path: str, text: str) -> List[dict]:
    """[{"spec", "line", "quote", "dynamic"}], ordered by line. Empty when the file does not parse."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return []
    lines = text.splitlines()
    consumed: Set[int] = set()
    found: Dict[Tuple[int, str], dict] = {}
    for node in ast.walk(tree):  # breadth first: a parent is seen before the text inside it
        if id(node) in consumed:
            continue
        hit = _read(node, consumed)
        if hit is None:
            continue
        spec, line = hit
        quote = lines[line - 1].strip()[:MAX_QUOTE_CHARS] if 1 <= line <= len(lines) else ""
        found.setdefault((line, spec), {"spec": spec, "line": line, "quote": quote, "dynamic": is_template(spec)})
    return [found[key] for key in sorted(found)]


def _text(node: ast.AST) -> Optional[str]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _read(node: ast.AST, consumed: Set[int]) -> Found:
    if isinstance(node, ast.Expr) and _text(node.value) is not None:
        consumed.add(id(node.value))  # a docstring or a bare string statement names no file
        return None
    if isinstance(node, ast.JoinedStr):
        consumed.update(id(value) for value in node.values)
        spec = "".join("{}" if _text(value) is None else _text(value) for value in node.values)
        return (spec, node.lineno) if is_path(spec) else None
    if isinstance(node, ast.Call) and _is_join(node.func):
        return _joined(list(node.args), consumed)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _joined(_division_operands(node), consumed)
    text = _text(node)
    return (text, node.lineno) if text is not None and is_path(text) else None


def _is_join(func: ast.AST) -> bool:
    if isinstance(func, ast.Name):
        return func.id == "join"
    return isinstance(func, ast.Attribute) and func.attr in _JOIN_NAMES and _text(func.value) is None


def _division_operands(node: ast.BinOp) -> List[ast.AST]:
    """`a / "b" / "c"` as [a, "b", "c"]."""
    operands = [node.right]
    left = node.left
    while isinstance(left, ast.BinOp) and isinstance(left.op, ast.Div):
        operands.append(left.right)
        left = left.left
    operands.append(left)
    return operands[::-1]


def _joined(operands: Sequence[ast.AST], consumed: Set[int]) -> Found:
    """The trailing run of text operands joined with `/`; operands before it are dropped."""
    run = []
    for operand in reversed(operands):
        if _text(operand) is None:
            break
        run.append(operand)
    if not run:
        return None
    spec = "/".join(piece for piece in (_text(operand).strip("/") for operand in reversed(run)) if piece)
    if not is_path(spec):
        return None
    consumed.update(id(operand) for operand in run)
    return spec, run[0].lineno
