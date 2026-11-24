"""Template files other code reads (Terraform `.tftpl`): graph nodes without imports.

A template is read by `templatefile()`; making it a node lets that reference
resolve to a file instead of being counted as a target the scan did not parse.
"""
from __future__ import annotations

from typing import List


def extract(path: str, text: str) -> dict:
    return {"imports": [], "symbols": []}


def build_context(parsed_paths, all_paths, read_path) -> None:
    return None


def resolve(import_record: dict, importer_path: str, context) -> List[dict]:
    return []
