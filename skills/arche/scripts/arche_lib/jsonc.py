"""Read JSON that allows comments and trailing commas (tsconfig.json, jsconfig.json)."""
from __future__ import annotations

import json
from typing import Any, List, Optional


def _skip_string(text: str, index: int) -> int:
    """`index` is at an opening quote; return the offset just past the closing quote."""
    index += 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
        elif text[index] == '"':
            return index + 1
        else:
            index += 1
    return index


def _skip_comment(text: str, index: int) -> int:
    if text.startswith("//", index):
        stop = text.find("\n", index)
        return len(text) if stop == -1 else stop
    stop = text.find("*/", index + 2)
    return len(text) if stop == -1 else stop + 2


def _next_significant(text: str, index: int) -> str:
    """First character at or after `index` that is not whitespace or a comment."""
    while index < len(text):
        if text[index].isspace():
            index += 1
        elif text.startswith(("//", "/*"), index):
            index = _skip_comment(text, index)
        else:
            return text[index]
    return ""


def strip(text: str) -> str:
    """Remove comments and trailing commas, leaving strings untouched."""
    kept: List[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            stop = _skip_string(text, index)
            kept.append(text[index:stop])
            index = stop
        elif text.startswith(("//", "/*"), index):
            index = _skip_comment(text, index)
        elif char == "," and _next_significant(text, index + 1) in ("}", "]"):
            index += 1
        else:
            kept.append(char)
            index += 1
    return "".join(kept)


def loads(text: Optional[str]) -> Any:
    """Parsed value, or None when the text is missing or not valid JSON-with-comments."""
    if text is None:
        return None
    try:
        return json.loads(strip(text.lstrip("﻿")))
    except ValueError:
        return None
