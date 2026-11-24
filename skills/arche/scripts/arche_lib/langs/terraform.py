"""Terraform (.tf) files: top-level blocks as symbols, module sources as imports.

A Terraform module is a directory. A local `source = "./modules/vpc"` resolves to
that directory's representative file, main.tf when present and otherwise the
first .tf file by name; the edge carries the directory as `module_dir` so change
analysis can treat the other files there as part of the same module. Registry,
Git, HTTP and bucket sources are external.
"""
from __future__ import annotations

import posixpath
from typing import Dict, FrozenSet, List, NamedTuple, Optional, Sequence

from .hcl_lexer import Token, tokens

MAX_QUOTE_CHARS = 200
MAIN_FILE = "main.tf"
SUFFIX = ".tf"
# Block type -> (symbol name prefix, how many labels name it, exported)
_BLOCKS = {
    "resource": ("", 2, False),
    "data": ("data.", 2, False),
    "module": ("module.", 1, False),
    "variable": ("var.", 1, True),
    "output": ("output.", 1, True),
}


def _quote(lines: Sequence[str], line: int) -> str:
    return lines[line - 1].strip()[:MAX_QUOTE_CHARS] if 1 <= line <= len(lines) else ""


def _header(toks: Sequence[Token], start: int):
    """(block type, labels, index of `{`) when toks[start] opens a block, else None."""
    if toks[start].kind != "ident":
        return None
    labels: List[str] = []
    j = start + 1
    while j < len(toks) and toks[j].kind in ("string", "ident") and not (
            toks[j].kind == "ident" and j + 1 < len(toks) and toks[j + 1].value == "="):
        labels.append(toks[j].value)
        j += 1
    if j < len(toks) and toks[j].kind == "punct" and toks[j].value == "{":
        return toks[start].value, labels, j
    return None


def extract(path: str, text: str) -> dict:
    toks, broken = tokens(text)
    lines = text.splitlines()
    symbols: List[dict] = []
    imports: List[dict] = []
    depth = 0
    module_depth: Optional[int] = None  # depth inside the current top-level module block
    i = 0
    while i < len(toks):
        token = toks[i]
        if token.kind == "punct" and token.value in "{[(":
            depth += 1
        elif token.kind == "punct" and token.value in "}])":
            depth = max(depth - 1, 0)
            if depth == 0:
                module_depth = None
        elif depth == 0 and token.kind == "ident":
            header = _header(toks, i)
            if header is not None:
                kind, labels, brace = header
                spec = _BLOCKS.get(kind)
                if spec is not None and len(labels) >= spec[1]:
                    prefix, count, exported = spec
                    name = prefix + ".".join(labels[:count])
                    if all(symbol["name"] != name for symbol in symbols):
                        symbols.append({"name": name, "kind": kind, "line": token.line, "exported": exported})
                module_depth = 1 if kind == "module" else None
                depth = 1
                i = brace + 1
                continue
        elif (module_depth == 1 and depth == 1 and token.kind == "ident" and token.value == "source"
              and i + 2 < len(toks) and toks[i + 1].value == "=" and toks[i + 2].kind == "string"):
            value = toks[i + 2]
            imports.append(_import(path, value, token.line, lines))
        i += 1
    result = {"imports": imports, "symbols": symbols}
    return {**result, "degraded": True} if broken else result


def _import(path: str, value: Token, line: int, lines: Sequence[str]) -> dict:
    record = {"kind": "module", "spec": value.value, "names": [], "line": line,
              "quote": _quote(lines, line), "dynamic": value.templated}
    if _is_local(value.value) and not value.templated:
        directory = posixpath.normpath(posixpath.join(posixpath.dirname(path), value.value))
        if directory != ".." and not directory.startswith("../"):
            record["attrs"] = {"module_dir": "" if directory == "." else directory}
    return record


def _is_local(spec: str) -> bool:
    return spec.startswith(("./", "../")) or spec in (".", "..")


class Context(NamedTuple):
    all_paths: FrozenSet[str]
    modules: FrozenSet[str]
    tf_by_dir: Dict[str, List[str]]   # directory -> sorted .tf paths in it


def build_context(parsed_paths, all_paths, read_path) -> Context:
    by_dir: Dict[str, List[str]] = {}
    for path in sorted(all_paths):
        if path.endswith(SUFFIX):
            by_dir.setdefault(posixpath.dirname(path), []).append(path)
    return Context(all_paths=frozenset(all_paths), modules=frozenset(parsed_paths), tf_by_dir=by_dir)


def _unresolved(spec: str, reason: str) -> dict:
    return {"status": "unresolved", "spec": spec, "reason": reason}


def representative(files: Sequence[str]) -> str:
    """main.tf when the module has one, else the first .tf file by name."""
    return next((path for path in files if posixpath.basename(path) == MAIN_FILE), files[0])


def resolve(imp: dict, importer: str, ctx: Context) -> List[dict]:
    spec = imp["spec"]
    if imp.get("dynamic"):
        return [_unresolved(spec, "dynamic")]
    if _is_local(spec):
        directory = imp.get("attrs", {}).get("module_dir")
        files = ctx.tf_by_dir.get(directory, []) if directory is not None else []
        if not files:
            return [_unresolved(spec, "module_not_found")]
        target = representative(files)
        if target not in ctx.modules:
            return [_unresolved(spec, "target_skipped")]
        return [{"status": "verified", "to": target, "names": [], "rule": "module_dir"}]
    return [{"status": "external", "package": package(spec)}]


def package(spec: str) -> str:
    """The source without `?ref=...` and without a `//subdir` inside the package."""
    base = spec.split("?", 1)[0]
    scheme_end = base.find("://")
    start = scheme_end + 3 if scheme_end >= 0 else 0
    if base.startswith(("git::", "hg::", "s3::", "gcs::")) and scheme_end < 0:
        start = base.find("::") + 2
    sub = base.find("//", start)
    return base[:sub] if sub >= 0 else base
