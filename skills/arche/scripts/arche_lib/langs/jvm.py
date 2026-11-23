"""Java and Kotlin: imports by fully qualified name, plus same-package type references.

Both languages share one context, so a Kotlin file can import a Java class and
the reverse. Files in one package use each other without an import statement;
those links are recorded as inferred from a type name that appears in the code.
"""
from __future__ import annotations

import posixpath
import re
from typing import NamedTuple

from ..lexer import Rules, depth_at, line_of, mask
from .jvm_http import routes
from .pathutil import source_line

_JAVA = Rules(templates=False, regex=False, word_quote_guard=False, nested_comments=False,
              interpolation=False, triple_quotes=True, raw_prefix=False)
_KOTLIN = Rules(templates=False, regex=False, word_quote_guard=False, nested_comments=True,
                interpolation=True, triple_quotes=True, raw_prefix=False)

_NAME = r"[A-Za-z_$][\w$]*"
_PATH = rf"{_NAME}(?:\s*\.\s*{_NAME})*"
_ANNOTATIONS = r"(?:@[\w.:]+(?:\([^)\n]*\))?\s+)*"
_MODIFIERS = (
    r"(?:(?:public|private|protected|internal|open|abstract|final|sealed|non-sealed|static|strictfp|data|enum|"
    r"annotation|inline|value|inner|const|lateinit|override|suspend|operator|infix|tailrec|external|expect|"
    r"actual|synchronized|native|transient|volatile|default)\s+)*"
)
_PACKAGE = re.compile(rf"(?m)^[ \t]*package\s+({_PATH})")
_IMPORT = re.compile(rf"(?m)^[ \t]*import\s+(static\s+)?({_PATH})(\s*\.\s*\*)?(?:\s+as\s+({_NAME}))?")
_JAVA_DECL = re.compile(rf"(?m)^[ \t]*{_ANNOTATIONS}({_MODIFIERS})(class|interface|enum|record|@interface)\s+({_NAME})")
_KOTLIN_DECL = re.compile(rf"(?m)^[ \t]*{_ANNOTATIONS}({_MODIFIERS})(fun\s+interface|class|interface|object|typealias|fun|val|var)\b[ \t]*([^\n]*)")
_KOTLIN_MEMBER = re.compile(rf"(?:<[^>]*>\s*)?(?:[\w<>?,.\s]*\.)?({_NAME})")
_TYPE_REFERENCE = re.compile(r"(?<![\w$.])[A-Z][\w$]*")
_CALL = re.compile(r"(?<![\w$.])([a-z_][\w$]*)\s*\(")
_LOCAL_FUNCTION = re.compile(rf"\bfun\s+(?:<[^>]*>\s*)?(?:[\w<>?,.\s]*\.)?({_NAME})\s*\(")
_KINDS = {"class": "class", "interface": "interface", "enum": "enum", "record": "class", "@interface": "class",
          "object": "class", "typealias": "typedef", "fun": "function", "val": "variable", "var": "variable"}
_TYPE_KINDS = frozenset({"class", "interface", "enum", "typedef"})


def _is_kotlin(path):
    return path.endswith((".kt", ".kts"))


def _java_symbols(masked):
    for match in _JAVA_DECL.finditer(masked.text):
        if depth_at(masked, match.start()) == 0:
            modifiers = match.group(1).split()
            yield match.group(3), _KINDS[match.group(2)], match.start(3), "public" in modifiers


def _kotlin_symbols(masked):
    for match in _KOTLIN_DECL.finditer(masked.text):
        if depth_at(masked, match.start()) != 0:
            continue
        modifiers = match.group(1).split()
        keyword = "interface" if match.group(2).startswith("fun") and match.group(2) != "fun" else match.group(2)
        name = _KOTLIN_MEMBER.match(match.group(3)) if keyword in ("fun", "val", "var") else re.match(_NAME, match.group(3))
        if not name:
            continue  # destructuring declarations and backtick names
        kind = "enum" if keyword == "class" and "enum" in modifiers else _KINDS[keyword]
        group = 1 if keyword in ("fun", "val", "var") else 0
        yield name.group(group), kind, match.start(3) + name.start(group), not {"private", "internal"} & set(modifiers)


def extract(path, text):
    kotlin = _is_kotlin(path)
    masked = mask(text, _KOTLIN if kotlin else _JAVA)
    lines = text.splitlines()
    header = _PACKAGE.search(masked.text)
    package = re.sub(r"\s+", "", header.group(1)) if header else ""
    body_start = header.end() if header else 0

    imports, imported = [], set()
    for match in _IMPORT.finditer(masked.text):
        if depth_at(masked, match.start()) != 0:
            continue
        spec = re.sub(r"\s+", "", match.group(2))
        line = line_of(masked, match.start())
        body_start = max(body_start, match.end())
        wildcard = bool(match.group(3))
        imported.add(match.group(4) or spec.rsplit(".", 1)[-1])
        imports.append({"spec": spec + (".*" if wildcard else ""), "line": line, "quote": source_line(lines, line),
                        "names": [] if wildcard else [spec.rsplit(".", 1)[-1]],
                        "attrs": {"alias": match.group(4)} if match.group(4) else {}})

    symbols, declared = [], set()
    for name, kind, offset, exported in (_kotlin_symbols if kotlin else _java_symbols)(masked):
        if name not in declared:
            declared.add(name)
            symbols.append({"name": name, "kind": kind, "line": line_of(masked, offset), "exported": exported})

    # A capitalized name that is neither imported nor declared here may be a type
    # from the same package. The resolver keeps only the names that really are.
    seen = set(imported) | declared
    candidates = [(match.start(), match.group(0)) for match in _TYPE_REFERENCE.finditer(masked.text, body_start)]
    if kotlin:
        # A bare call may reach a top-level function of the same package. Names this
        # file defines at any depth are left out, since a member or local function wins.
        seen |= {match.group(1) for match in _LOCAL_FUNCTION.finditer(masked.text)}
        candidates += [(match.start(1), match.group(1)) for match in _CALL.finditer(masked.text, body_start)]
    for offset, name in sorted(candidates):
        if name in seen:
            continue
        seen.add(name)
        line = line_of(masked, offset)
        imports.append({"spec": name, "line": line, "quote": source_line(lines, line), "names": [name], "implicit": True})
    references = {match.group(0) for match in _TYPE_REFERENCE.finditer(masked.text, body_start)}
    if kotlin:
        references |= {match.group(1) for match in _CALL.finditer(masked.text, body_start)}
    return {"imports": imports, "symbols": symbols, "package": package, "references": references,
            "http_routes": routes(path, text, masked)}


class Context(NamedTuple):
    declared: dict      # fully qualified name -> files that declare it
    types: dict         # fully qualified type name -> files that declare it
    packages: dict      # package -> files in it
    package_of: dict    # file -> package
    references: dict    # file -> capitalized names used in its code
    type_names: dict    # file -> type names it declares
    functions: dict     # fully qualified Kotlin top-level function -> files that declare it


def build_context(parsed_paths, all_paths, read_path):
    declared, types, packages, package_of, references, type_names, functions = {}, {}, {}, {}, {}, {}, {}
    for path in sorted(parsed_paths):
        try:
            result = extract(path, read_path(path) or "")
        except Exception:  # a file the graph step already reported
            continue
        package = result["package"]
        package_of[path] = package
        references[path] = result["references"]
        # Names a wildcard import can bring in: types, and Kotlin top-level functions.
        type_names[path] = {symbol["name"] for symbol in result["symbols"] if symbol["kind"] in _TYPE_KINDS or symbol["kind"] == "function"}
        packages.setdefault(package, []).append(path)
        prefix = package + "." if package else ""
        for symbol in result["symbols"]:
            declared.setdefault(prefix + symbol["name"], []).append(path)
            if symbol["kind"] in _TYPE_KINDS:
                types.setdefault(prefix + symbol["name"], []).append(path)
            elif symbol["kind"] == "function":
                functions.setdefault(prefix + symbol["name"], []).append(path)
        # Java names a public type after its file; cover a declaration the pattern missed.
        stem = posixpath.basename(path).rsplit(".", 1)[0]
        if not _is_kotlin(path) and path not in declared.get(prefix + stem, ()):
            declared.setdefault(prefix + stem, []).append(path)
    return Context(declared, types, packages, package_of, references, type_names, functions)


def _targets(paths, names, rule):
    status = "verified" if len(paths) == 1 else "inferred"
    return [{"status": status, "to": path, "names": names, "rule": rule if status == "verified" else rule + "_candidates"}
            for path in paths]


def _external_package(spec):
    """The package part of a name: everything before the first capitalized segment."""
    parts = spec.removesuffix(".*").split(".")
    for index, part in enumerate(parts):
        if part[:1].isupper():
            return ".".join(parts[:index]) or spec
    return ".".join(parts[:-1]) if len(parts) > 1 and not spec.endswith(".*") else ".".join(parts)


def resolve(imp, importer, ctx):
    spec = imp["spec"]
    if imp.get("implicit"):
        package = ctx.package_of.get(importer, "")
        index = ctx.types if spec[:1].isupper() else ctx.functions
        paths = [path for path in index.get((package + "." if package else "") + spec, ()) if path != importer]
        return [{"status": "inferred", "to": path, "names": [spec], "rule": "jvm_same_package"} for path in paths]

    if spec.endswith(".*"):
        base = spec[:-2]
        if base in ctx.declared:  # every member of one type
            return _targets(ctx.declared[base], [], "jvm_qualified_name")
        if base in ctx.packages:
            paths = [path for path in ctx.packages[base] if path != importer]
            # Keep the files whose types the importer names; without a match, every file stays a candidate.
            used = ctx.references.get(importer, set())
            paths = [path for path in paths if ctx.type_names[path] & used] or paths
            return [{"status": "inferred", "to": path, "names": [], "rule": "jvm_package_wildcard"} for path in paths]
        return [{"status": "external", "package": _external_package(spec)}]

    # A nested type or a static member is declared in the file of its outermost type.
    parts = spec.split(".")
    for end in range(len(parts), 0, -1):
        name = ".".join(parts[:end])
        if name in ctx.declared:
            return _targets(ctx.declared[name], imp["names"], "jvm_qualified_name")
    if any(".".join(parts[:end]) in ctx.packages for end in range(len(parts) - 1, 0, -1)):
        return [{"status": "unresolved", "spec": spec, "reason": "symbol_not_found"}]
    return [{"status": "external", "package": _external_package(spec)}]
