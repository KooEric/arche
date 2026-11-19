"""Read repository files, refusing content that changed since the scan."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Optional

from .errors import IntegrityError
from .paths import is_within


def make_reader(inventory: dict) -> Callable[[str], Optional[str]]:
    """Return read_path(rel): the file's text, or None for paths the scan did not hash."""
    root = Path(inventory["repo"]["root"])
    hashes = {entry["path"]: entry["sha256"] for entry in inventory["files"] if "sha256" in entry}

    def read_path(rel: str) -> Optional[str]:
        expected = hashes.get(rel)
        if expected is None:
            return None
        path = root / rel
        if path.is_symlink() or not is_within(root, path):
            raise IntegrityError(f"unsafe source path: {rel}")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise IntegrityError(f"cannot read {rel}: {exc}") from None
        if hashlib.sha256(data).hexdigest() != expected:
            raise IntegrityError(f"file changed since scan: {rel}")
        return data.decode("utf-8", errors="replace")

    return read_path
