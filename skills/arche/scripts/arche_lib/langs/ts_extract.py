"""TypeScript/JavaScript imports and top-level symbols, read from masked source text.

Symbols are declarations that begin a line at bracket depth zero. Exports made
through a separate `export { a, b }` list do not set `exported`.
"""
from __future__ import annotations

import re
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from ..lexer import JS, Masked, depth_at, line_of, mask
from .pathutil import source_line

_NOT_MEMBER = r"(?<![\w$.])"
_IMPORT = re.compile(_NOT_MEMBER + r"import\b")
_SIDE_EFFECT = re.compile(r"\s*(?=['\"])")
_CLAUSE = re.compile(r"\s+(type\s+(?!from\b))?([^;'\"()]*?)\s*\bfrom\s*(?=['\"])")
_CALL_OPEN = re.compile(r"\s*\(\s*")
_AFTER_ARGUMENT = re.compile(r"\s*[),]")
_EXPORT_FROM = re.compile(
    _NOT_MEMBER + r"export\s+(type\s+)?(\*(?:\s+as\s+[\w$]+)?|\{[^{}]*\})\s*from\s*(?=['\"])"
)
_REQUIRE = re.compile(_NOT_MEMBER + r"require\s*\(\s*")
_BRACES = re.compile(r"\{([^{}]*)\}")
_NAMESPACE = re.compile(r"\*\s+as\s+([\w$]+)")
_SPECIFIER = re.compile(r"(?:type\s+)?([\w$]+)(?:\s+as\s+([\w$]+))?")
_IDENTIFIER = re.compile(r"[\w$]+")
_DECLARATION = re.compile(
    r"[ \t]*(export\s+)?(default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
    r"(function\s*\*?|class|interface|type|enum|const\s+enum|const|let|var|namespace|module)"
    r"\s+([A-Za-z_$][\w$]*)"
)
_DEFAULT_EXPORT = re.compile(r"[ \t]*export\s+default\b")
_KINDS = {
    "function": "function", "class": "class", "interface": "type", "type": "type",
    "enum": "enum", "const": "variable", "let": "variable", "var": "variable",
    "namespace": "namespace", "module": "namespace",
}
_NOT_NAMES = frozenset({"extends", "implements"})


def extract(path: str, text: str) -> dict:
    """Return {"imports", "symbols"} and `degraded: True` when brackets do not balance."""
    masked = mask(text, JS)
    lines = text.splitlines()
    found = sorted(_imports(masked, lines), key=lambda item: item[0])
    imports = [record for _offset, record in found]
    if not masked.balanced:
        return {"imports": imports, "symbols": [], "degraded": True}
    return {"imports": imports, "symbols": _symbols(masked)}


def _record(masked: Masked, lines: Sequence[str], offset: int, kind: str, spec: Optional[str],
            names: Sequence[str] = (), attrs: Optional[dict] = None, **flags: bool) -> Tuple[int, dict]:
    line = line_of(masked, offset)
    record = {
        "kind": kind, "spec": spec, "names": list(names), "line": line,
        "quote": source_line(lines, line), "attrs": attrs or {},
        "type_only": False, "dynamic": False, "conditional": False, "reexport": False,
    }
    return offset, {**record, **flags}


def _literal_argument(masked: Masked, offset: int) -> Optional[str]:
    """The string that is the whole first call argument at `offset`, if there is one."""
    body = masked.strings.get(offset)
    if body is None or "${" in body:
        return None
    after = offset + len(body) + 2
    return body if _AFTER_ARGUMENT.match(masked.text, after) else None


def _parse_clause(clause: str) -> Tuple[List[str], dict]:
    """Names imported by `import <clause> from`, plus aliases and a namespace prefix."""
    names: List[str] = []
    aliases: Dict[str, str] = {}
    attrs: dict = {}
    braces = _BRACES.search(clause)
    outside = clause[:braces.start()] + clause[braces.end():] if braces else clause
    namespace = _NAMESPACE.search(outside)
    if namespace:
        attrs["prefix"] = namespace.group(1)
        outside = outside.replace(namespace.group(0), "")
    default = outside.strip(" ,\t\r\n")
    if _IDENTIFIER.fullmatch(default):
        names.append("default")
        aliases[default] = "default"
    for item in (braces.group(1).split(",") if braces else []):
        specifier = _SPECIFIER.fullmatch(item.strip())
        if specifier:
            names.append(specifier.group(1))
            if specifier.group(2) and specifier.group(2) != specifier.group(1):
                aliases[specifier.group(2)] = specifier.group(1)
    if namespace:
        names.append("*")
    return names, ({**attrs, "aliases": aliases} if aliases else attrs)


def _call(masked: Masked, lines: Sequence[str], start: int, argument: int, kind: str) -> Tuple[int, dict]:
    spec = _literal_argument(masked, argument)
    nested = depth_at(masked, start) > 0
    is_dynamic = kind == "dynamic" or spec is None
    return _record(masked, lines, start, kind, spec, dynamic=is_dynamic, conditional=nested)


def _import_statement(masked: Masked, lines: Sequence[str], match: "re.Match") -> Optional[Tuple[int, dict]]:
    start, end = match.start(), match.end()
    side_effect = _SIDE_EFFECT.match(masked.text, end)
    if side_effect:
        return _record(masked, lines, start, "import", masked.strings.get(side_effect.end()))
    clause = _CLAUSE.match(masked.text, end)
    if clause:
        names, attrs = _parse_clause(clause.group(2))
        spec = masked.strings.get(clause.end())
        return _record(masked, lines, start, "import", spec, names, attrs, type_only=bool(clause.group(1)))
    call = _CALL_OPEN.match(masked.text, end)
    if call:
        return _call(masked, lines, start, call.end(), "dynamic")
    return None


def _export_names(clause: str) -> List[str]:
    if clause.startswith("*"):
        return ["*"]
    items = (_SPECIFIER.fullmatch(item.strip()) for item in clause.strip("{}").split(","))
    return [item.group(1) for item in items if item]


def _imports(masked: Masked, lines: Sequence[str]) -> Iterator[Tuple[int, dict]]:
    for match in _IMPORT.finditer(masked.text):
        found = _import_statement(masked, lines, match)
        if found and (found[1]["spec"] is not None or found[1]["dynamic"]):
            yield found
    for match in _EXPORT_FROM.finditer(masked.text):
        spec = masked.strings.get(match.end())
        if spec is not None:
            yield _record(masked, lines, match.start(), "export", spec, _export_names(match.group(2)),
                          type_only=bool(match.group(1)), reexport=True)
    for match in _REQUIRE.finditer(masked.text):
        yield _call(masked, lines, match.start(), match.end(), "require")


def _kind(keyword: str) -> str:
    words = keyword.replace("*", " ").split()
    return "enum" if words[-1] == "enum" else _KINDS[words[0]]


def _symbols(masked: Masked) -> List[dict]:
    seen = set()
    symbols: List[dict] = []
    for index, start in enumerate(masked.line_starts):
        if masked.line_depths[index] != 0:
            continue
        found = []
        declaration = _DECLARATION.match(masked.text, start)
        if declaration and declaration.group(4) not in _NOT_NAMES:
            found.append((declaration.group(4), _kind(declaration.group(3)), bool(declaration.group(1))))
        if _DEFAULT_EXPORT.match(masked.text, start):
            found.append(("default", "default", True))
        for name, kind, exported in found:
            if name not in seen:
                seen.add(name)
                symbols.append({"name": name, "kind": kind, "line": index + 1, "exported": exported})
    return symbols
