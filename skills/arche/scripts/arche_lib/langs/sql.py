"""SQL and HQL files: graph nodes plus the tables each file reads and writes."""
from __future__ import annotations

from typing import List

from .sql_tables import tables


def extract(path: str, text: str) -> dict:
    found = tables(path, text)
    degraded = {"degraded": True} if found["degraded"] else {}
    return {"imports": [], "symbols": [], "tables": found["tables"], **degraded}


def build_context(parsed_paths, all_paths, read_path) -> None:
    return None


def resolve(import_record: dict, importer_path: str, context) -> List[dict]:
    return []
