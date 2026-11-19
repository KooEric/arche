"""Maps a language name to its adapter module.

Every adapter provides:
    extract(path, text) -> {"imports": [...], "symbols": [...]}   (raises ParseError)
    build_context(parsed_paths, all_paths, read_path) -> context
    resolve(import_record, importer_path, context) -> [result, ...]

Languages that share an adapter also share one context, so a TypeScript file
can import a JavaScript file and the reverse.
"""
from __future__ import annotations

from . import dart, go, python, typescript

ADAPTERS = {
    "python": python,
    "typescript": typescript,
    "javascript": typescript,
    "dart": dart,
    "go": go,
}


def adapter_for(lang: str):
    return ADAPTERS[lang]
