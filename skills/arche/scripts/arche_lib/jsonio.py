"""Deterministic JSON output and checked JSON input."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import IntegrityError


def dumps(data: Any) -> str:
    """Serialize so that equal data always produces identical bytes."""
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_json(path: Path, data: Any) -> None:
    write_text(path, dumps(data))


def write_text(path: Path, content: str) -> None:
    """Write atomically: a crash never leaves a half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temp_name, path)
    except BaseException:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise


def read_json(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise IntegrityError(f"missing input file: {path}") from None
    except json.JSONDecodeError as exc:
        raise IntegrityError(f"invalid JSON in {path}: {exc}") from None
