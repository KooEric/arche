"""Maps a language name to its adapter module.

Every adapter provides:
    extract(path, text) -> {"imports": [...], "symbols": [...]}   (raises ParseError)
    build_context(parsed_paths, all_paths, read_path) -> context
    resolve(import_record, importer_path, context) -> [result, ...]

An adapter that takes run-time options lists their names in OPTIONS; build_context
then receives each given option as a keyword argument.

Languages that share an adapter also share one context, so a TypeScript file
can import a JavaScript file and the reverse.
"""
from __future__ import annotations

from . import dart, go, jsonnet, jvm, python, sql, terraform, typescript

ADAPTERS = {
    "python": python,
    "typescript": typescript,
    "javascript": typescript,
    "dart": dart,
    "go": go,
    "java": jvm,
    "kotlin": jvm,
    "sql": sql,
    "jsonnet": jsonnet,
    "terraform": terraform,
}


def adapter_for(lang: str):
    return ADAPTERS[lang]
