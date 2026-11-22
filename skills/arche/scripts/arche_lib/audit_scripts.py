"""Unused file-private declarations in TypeScript, JavaScript, Dart and Go. Review candidates only.

A declaration is reported when its name appears once in the code that could reach it and in no
string literal: the file for TypeScript and JavaScript, the library file for Dart, the package
directory for Go. Tests, annotated declarations and entry-point names are left out.
"""
import posixpath
import re
from collections import Counter, defaultdict

from .langs.go import _RULES as GO
from .lexer import DART, JS, mask

_RULES = {"typescript": JS, "javascript": JS, "dart": DART, "go": GO}
_WORD = re.compile(r"[A-Za-z_$][\w$]*")
_PLAIN_WORD = re.compile(r"[A-Za-z_]\w*")
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|specs?|e2e|fixtures|examples|samples|generated|testdata)(/|$)|\.(test|spec|d)\.[^/.]+$|_test\.(go|dart)$")
_DART_PARTS = re.compile(r"(?m)^\s*part\s")
_ENTRY_NAMES = frozenset({"main", "init", "default", "setup", "teardown"})
_KINDS = frozenset({"function", "class", "variable", "type", "enum", "typedef", "interface", "mixin", "extension"})


def _annotated(lines, line):
    """True when the line above the declaration is an annotation or decorator."""
    index = line - 2
    while index >= 0 and not lines[index].strip():
        index -= 1
    return index >= 0 and lines[index].lstrip().startswith("@")


def build_script_audit(graph, read_path):
    langs = {node["id"][5:]: node["lang"] for node in graph["nodes"] if node["type"] == "file" and node.get("lang") in _RULES}
    words, strings, lines, text_of = {}, defaultdict(set), {}, {}
    for path, lang in sorted(langs.items()):
        text = read_path(path)
        if text is None:
            continue
        item = mask(text, _RULES[lang])
        words[path] = Counter(_WORD.findall(item.text))
        lines[path] = item.text.split("\n")
        text_of[path] = item.text
        for literal in item.strings.values():
            strings[lang].update(_PLAIN_WORD.findall(literal))
    packages = defaultdict(Counter)  # Go: every file of a directory is one package
    for path, counter in words.items():
        if langs[path] == "go":
            packages[posixpath.dirname(path)].update(counter)

    findings = []
    for node in graph["nodes"]:
        if node["type"] != "symbol" or node.get("exported") or node.get("kind") not in _KINDS:
            continue
        path, name = node["id"][4:].rsplit("#", 1)
        lang = langs.get(path)
        if lang is None or path not in words or _TEST_PATH.search(path) or name in _ENTRY_NAMES or name in strings[lang]:
            continue
        if lang == "dart" and (not name.startswith("_") or _DART_PARTS.search(text_of[path])):
            continue  # a part file shares private names with its library
        if lang == "go" and name.startswith(("Test", "Benchmark", "Example", "Fuzz")):
            continue
        seen = packages[posixpath.dirname(path)][name] if lang == "go" else words[path][name]
        if seen != 1 or _annotated(lines[path], node["line"]):
            continue
        findings.append({"rule": "unused-private-symbol", "path": path, "line": node["line"], "name": name})
    return sorted(findings, key=lambda f: (f["path"], f["line"]))
