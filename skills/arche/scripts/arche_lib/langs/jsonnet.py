"""Jsonnet and libsonnet files: import, importstr and importbin, resolved like the jsonnet tool.

Search order for `import "x"`:
  1. the importing file's directory (verified, rule relative);
  2. the library directories given with --jpath, right-most first, as `jsonnet -J`
     does (verified, rule jpath);
  3. without --jpath only: the repository's lib/ directories (inferred, rule
     jpath_guess; several matches are ambiguous);
  4. a jsonnet-bundler package (external), which lives under the vendored vendor/;
  5. otherwise unresolved.
"""
from __future__ import annotations

import posixpath
from typing import Dict, FrozenSet, List, NamedTuple, Optional, Sequence, Tuple

IMPORT_KINDS = frozenset({"import", "importstr", "importbin"})
LIB_DIR = "lib"
BUNDLER_FILE = "jsonnetfile.json"
MAX_QUOTE_CHARS = 200
# The jsonnet interpreter reads JSONNET_PATH and -J; both are run-time choices.
OPTIONS = ("jpath",)

_IDENT_START = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_IDENT = _IDENT_START | frozenset("0123456789")
_OPEN = frozenset("{[(")
_CLOSE = frozenset("}])")
_BUNDLER_HOSTS = ("github.com/", "gitlab.com/", "bitbucket.org/")


class Token(NamedTuple):
    kind: str   # "ident", "string" or "punct"
    value: str
    line: int


def _tokens(text: str) -> Tuple[List[Token], bool]:
    """Identifiers, string literals and punctuation, with comments dropped. The flag is
    True when a string, text block or comment does not end."""
    out: List[Token] = []
    i, line, n = 0, 1, len(text)
    while i < n:
        char = text[i]
        if char == "\n":
            line += 1
            i += 1
        elif char in " \t\r":
            i += 1
        elif char == "#" or text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                return out, True
            line += text.count("\n", i, end)
            i = end + 2
        elif text.startswith("|||", i):
            start_line = line
            end = _text_block_end(text, i)
            if end < 0:
                return out, True
            line += text.count("\n", i, end)
            out.append(Token("string", "", start_line))  # never a path: import takes a plain string
            i = end
        elif char in "\"'" or (char == "@" and i + 1 < n and text[i + 1] in "\"'"):
            verbatim = char == "@"
            quote = text[i + 1] if verbatim else char
            value, end = _string(text, i + (2 if verbatim else 1), quote, verbatim)
            if end < 0:
                return out, True
            out.append(Token("string", value, line))
            line += text.count("\n", i, end)
            i = end
        elif char in _IDENT_START:
            start = i
            while i < n and text[i] in _IDENT:
                i += 1
            out.append(Token("ident", text[start:i], line))
        else:
            out.append(Token("punct", char, line))
            i += 1
    return out, False


def _string(text: str, i: int, quote: str, verbatim: bool) -> Tuple[str, int]:
    """(value, index after the closing quote), or ("", -1) when the string does not end."""
    parts: List[str] = []
    n = len(text)
    while i < n:
        char = text[i]
        if char == quote:
            if verbatim and i + 1 < n and text[i + 1] == quote:
                parts.append(quote)
                i += 2
                continue
            return "".join(parts), i + 1
        if char == "\\" and not verbatim and i + 1 < n:
            nxt = text[i + 1]
            parts.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
            i += 2
            continue
        parts.append(char)
        i += 1
    return "", -1


def _text_block_end(text: str, i: int) -> int:
    """Index after the closing `|||` of the text block opened at i, or -1."""
    start = text.find("\n", i)
    if start < 0:
        return -1
    position = start + 1
    while position < len(text):
        end = text.find("\n", position)
        row = text[position:] if end < 0 else text[position:end]
        stripped = row.lstrip(" \t")
        if stripped.startswith("|||"):
            return position + (len(row) - len(stripped)) + 3
        if end < 0:
            return -1
        position = end + 1
    return -1


def _quote(lines: Sequence[str], line: int) -> str:
    return lines[line - 1].strip()[:MAX_QUOTE_CHARS] if 1 <= line <= len(lines) else ""


def extract(path: str, text: str) -> dict:
    tokens, degraded = _tokens(text)
    lines = text.splitlines()
    imports: List[dict] = []
    symbols: List[dict] = []
    depth = 0
    in_local = False
    for index, token in enumerate(tokens):
        nxt = tokens[index + 1] if index + 1 < len(tokens) else None
        if token.kind == "punct":
            if token.value in _OPEN:
                depth += 1
            elif token.value in _CLOSE:
                depth = max(depth - 1, 0)
            elif token.value == ";" and depth == 0:
                in_local = False
            continue
        if token.kind != "ident":
            continue
        if token.value in IMPORT_KINDS and nxt is not None and nxt.kind == "string":
            imports.append({"kind": token.value, "spec": nxt.value, "names": [], "line": token.line,
                            "quote": _quote(lines, token.line), "dynamic": False})
        elif depth == 0 and token.value == "local":
            in_local = True
            _local_name(tokens, index + 1, symbols)
        elif depth == 0 and in_local and index > 0 and tokens[index - 1].kind == "punct" and tokens[index - 1].value == ",":
            _local_name(tokens, index, symbols)
    result = {"imports": imports, "symbols": symbols}
    return {**result, "degraded": True} if degraded else result


def _local_name(tokens: Sequence[Token], index: int, symbols: List[dict]) -> None:
    """Record `name` from `name =` or `name(...) =` at tokens[index]."""
    if index + 1 >= len(tokens) or tokens[index].kind != "ident":
        return
    if tokens[index + 1].kind == "punct" and tokens[index + 1].value in "=(":
        name = tokens[index].value
        if all(symbol["name"] != name for symbol in symbols):
            symbols.append({"name": name, "kind": "local", "line": tokens[index].line, "exported": False})


class Context(NamedTuple):
    all_paths: FrozenSet[str]
    modules: FrozenSet[str]
    jpath: Tuple[str, ...]        # --jpath directories, right-most first
    lib_dirs: Tuple[str, ...]     # repository lib/ directories, used only without --jpath
    bundler: bool                 # a jsonnetfile.json is present


def build_context(parsed_paths, all_paths, read_path, jpath: Sequence[str] = ()) -> Context:
    paths = frozenset(all_paths)
    dirs = {posixpath.dirname(path) for path in paths}
    ancestors = set()
    for directory in dirs:
        while directory:
            ancestors.add(directory)
            directory = posixpath.dirname(directory)
    lib_dirs = tuple(sorted(d for d in ancestors if posixpath.basename(d) == LIB_DIR))
    return Context(
        all_paths=paths,
        modules=frozenset(parsed_paths),
        jpath=tuple(reversed([_clean_dir(d) for d in jpath])),
        lib_dirs=lib_dirs,
        bundler=any(posixpath.basename(path) == BUNDLER_FILE for path in paths),
    )


def _clean_dir(directory: str) -> str:
    cleaned = posixpath.normpath(directory.replace("\\", "/")).strip("/")
    return "" if cleaned == "." else cleaned


def _unresolved(spec: str, reason: str, candidates: Sequence[str] = ()) -> dict:
    return {"status": "unresolved", "spec": spec, "reason": reason,
            **({"candidates": list(candidates)} if candidates else {})}


def _under(base: str, spec: str) -> Optional[str]:
    joined = posixpath.normpath(posixpath.join(base, spec)) if base else posixpath.normpath(spec)
    return None if joined == ".." or joined.startswith("../") else joined


def _found(path: str, spec: str, ctx: Context, rule: str, status: str = "verified") -> dict:
    if path in ctx.modules:
        return {"status": status, "to": path, "names": [], "rule": rule}
    return _unresolved(spec, "target_skipped")


def resolve(imp: dict, importer: str, ctx: Context) -> List[dict]:
    spec = imp["spec"]
    if not spec or spec.startswith("/"):
        return [_unresolved(spec, "module_not_found")]
    relative = _under(posixpath.dirname(importer), spec)
    if relative is not None and relative in ctx.all_paths:
        return [_found(relative, spec, ctx, "relative")]
    for directory in ctx.jpath:
        path = _under(directory, spec)
        if path is not None and path in ctx.all_paths:
            return [_found(path, spec, ctx, "jpath")]
    if not ctx.jpath:
        hits = [path for path in (_under(d, spec) for d in ctx.lib_dirs) if path is not None and path in ctx.all_paths]
        if len(hits) == 1:
            return [_found(hits[0], spec, ctx, "jpath_guess", status="inferred")]
        if hits:
            return [_unresolved(spec, "ambiguous", hits)]
    if spec.startswith(_BUNDLER_HOSTS) or (ctx.bundler and not spec.startswith(".")):
        return [{"status": "external", "package": _package(spec)}]
    return [_unresolved(spec, "module_not_found")]


def _package(spec: str) -> str:
    """`github.com/grafana/jsonnet-libs/grafonnet/main.libsonnet` -> `github.com/grafana/jsonnet-libs`."""
    parts = spec.split("/")
    if spec.startswith(_BUNDLER_HOSTS) and len(parts) >= 3:
        return "/".join(parts[:3])
    return parts[0]
