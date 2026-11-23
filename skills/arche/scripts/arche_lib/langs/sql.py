"""SQL and HQL files: graph nodes only. Their contents are not parsed."""
from __future__ import annotations

from typing import List


def extract(path: str, text: str) -> dict:
    return {"imports": [], "symbols": []}


def build_context(parsed_paths, all_paths, read_path) -> None:
    return None


def resolve(import_record: dict, importer_path: str, context) -> List[dict]:
    return []
