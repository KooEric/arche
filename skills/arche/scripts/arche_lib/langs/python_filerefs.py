"""SQL file paths written as text in a Python file, read with the built-in ast module."""
from __future__ import annotations

import ast
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ..fileref import SUFFIXES, is_path, is_template

MAX_QUOTE_CHARS = 200
_JOIN_NAMES = ("join", "joinpath")
Found = Optional[Tuple[str, int]]


def file_refs(path: str, text: str, tree: Optional[ast.AST] = None) -> List[dict]:
    """[{"spec", "line", "quote", "dynamic"}], ordered by line. Empty when the file does not parse.

    A file whose text never spells a SQL suffix cannot hold one in a string literal, so it is not walked."""
    lowered = text.lower()
    if not any(suffix in lowered for suffix in SUFFIXES):
        return []
    if tree is None:
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
        operands, inner = _chain(node, ast.Div)
        hit = _joined(operands, consumed)
        if hit is not None:
            consumed.update(id(part) for part in inner)
        return hit
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _concatenated(node, consumed)
    text = _text(node)
    return (text, node.lineno) if text is not None and is_path(text) else None


def _is_join(func: ast.AST) -> bool:
    if isinstance(func, ast.Name):
        return func.id == "join"
    return isinstance(func, ast.Attribute) and func.attr in _JOIN_NAMES and _text(func.value) is None


def _chain(node: ast.BinOp, op: type) -> Tuple[List[ast.AST], List[ast.AST]]:
    """`a / "b" / "c"` as ([a, "b", "c"], [the inner `a / "b"` nodes])."""
    operands = [node.right]
    inner: List[ast.AST] = []
    left = node.left
    while isinstance(left, ast.BinOp) and isinstance(left.op, op):
        inner.append(left)
        operands.append(left.right)
        left = left.left
    operands.append(left)
    return operands[::-1], inner


def _concatenated(node: ast.BinOp, consumed: Set[int]) -> Found:
    """`"sql/" + name + ".sql"` as `sql/{}.sql`; only a chain that ends in a SQL file name."""
    hit = _concat_spec(node)
    if hit is None:
        return None
    spec, parts, line = hit
    consumed.update(id(part) for part in parts)
    return spec, line


def _concat_spec(node: ast.BinOp) -> Optional[Tuple[str, List[ast.AST], int]]:
    """(spec, the nodes the chain uses up, line) for a `+` chain that builds a SQL path, else None."""
    operands, inner = _chain(node, ast.Add)
    last = _text(operands[-1])
    if last is None or not last.lower().endswith(SUFFIXES):
        return None
    pieces = [_text(operand) for operand in operands]
    if any(piece is not None and piece.lower().endswith(SUFFIXES) for piece in pieces[:-1]):
        return None  # "a.sql" + "b.sql" is two paths, not one being built
    if all(piece is not None for piece in pieces):
        spec = "".join(pieces)  # type: ignore[arg-type]
    elif last.startswith("/"):
        spec = last
    else:
        spec = "".join("{}" if piece is None else piece for piece in pieces)
    if not is_path(spec):
        return None
    parts = [node] + inner + [operand for operand in operands if _text(operand) is not None]
    return spec, parts, operands[-1].lineno


def _joined(operands: Sequence[ast.AST], consumed: Set[int]) -> Found:
    """The trailing run of text operands joined with `/`; operands before it are dropped.

    The last operand may also be a `+` chain that builds a SQL path; it is the final piece."""
    tail: List[str] = []
    used: List[ast.AST] = []
    rest = list(operands)
    line = 0
    if rest and isinstance(rest[-1], ast.BinOp) and isinstance(rest[-1].op, ast.Add):
        hit = _concat_spec(rest[-1])
        if hit is None:
            return None
        spec, used, line = hit
        tail.append(spec.strip("/"))
        rest.pop()
    run = []
    for operand in reversed(rest):
        if _text(operand) is None:
            break
        run.append(operand)
    if not run and not tail:
        return None
    pieces = [_text(operand).strip("/") for operand in reversed(run)] + tail
    spec = "/".join(piece for piece in pieces if piece)
    if not is_path(spec):
        return None
    consumed.update(id(operand) for operand in run)
    consumed.update(id(part) for part in used)
    return spec, line or run[0].lineno
