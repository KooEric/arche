"""List a repository's files and decide which ones are analyzed."""
from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import List, Optional, Sequence

from . import SCHEMA_VERSION, gitinfo
from .langs import language_for
from .paths import is_within, safe_name

MAX_BYTES = 1_000_000
BINARY_SNIFF_BYTES = 8192
GENERATED_HEADER_LINES = 5
GIT_DIR = ".git"
VENDORED_DIRS = frozenset(
    {
        "node_modules",
        ".wrangler",
        ".turbo",
        ".vercel",
        ".cache",
        "coverage",
        "vendor",
        "Pods",
        "build",
        "dist",
        ".dart_tool",
        "__pycache__",
        ".venv",
        "venv",
        ".next",
        "target",
    }
)
SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.keystore",
    "id_rsa*",
    "credentials*.json",
    "google-services.json",
    "GoogleService-Info.plist",
)
GENERATED_PATTERNS = (
    "*.g.dart",
    "*.freezed.dart",
    "*.pb.*",
    "*.min.js",
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "go.sum",
)
GENERATED_MARKERS = ("GENERATED", "DO NOT EDIT", "@generated")


def count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _matches(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _has_generated_marker(text: str) -> bool:
    head = text.split("\n", GENERATED_HEADER_LINES)[:GENERATED_HEADER_LINES]
    return any(marker in line for line in head for marker in GENERATED_MARKERS)


def _skipped(rel: str, reason: str, **measured: object) -> dict:
    return {"path": rel, "status": "skipped", "reason": reason, **measured}


def _text_skip_reason(rel: str, name: str, text: str) -> Optional[str]:
    if any(part in VENDORED_DIRS for part in PurePosixPath(rel).parts[:-1]):
        return "vendored"
    if _matches(name, GENERATED_PATTERNS) or _has_generated_marker(text):
        return "generated"
    if language_for(rel) is None:
        return "unsupported_lang"
    return None


def classify(root: Path, rel: str) -> dict:
    """Build the inventory entry for one path. Secret files are never opened."""
    full = root / rel
    name = PurePosixPath(rel).name
    if os.path.islink(full) or not is_within(root, full):
        return _skipped(rel, "symlink")
    if os.path.isdir(full):
        return _skipped(rel, "vendored" if name in VENDORED_DIRS else "submodule")
    if _matches(name, SECRET_PATTERNS):
        return _skipped(rel, "secret")
    try:
        size = os.path.getsize(full)
        if size > MAX_BYTES:
            return _skipped(rel, "too_large", bytes=size)
        data = full.read_bytes()
    except OSError:
        return _skipped(rel, "unreadable")
    if b"\0" in data[:BINARY_SNIFF_BYTES]:
        return _skipped(rel, "binary", bytes=size)
    text = data.decode("utf-8", errors="replace")
    measured = {
        "bytes": size,
        "lines": count_lines(text),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    reason = _text_skip_reason(rel, name, text)
    if reason is not None:
        return _skipped(rel, reason, **measured)
    return {"path": rel, "status": "analyzed", "lang": language_for(rel), **measured}


def walk_files(root: Path) -> List[str]:
    """List files without git. Vendored and linked directories become one entry."""
    found: List[str] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = Path(current).relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        descend = []
        for dirname in sorted(dirnames):
            if dirname == GIT_DIR:
                continue
            if dirname in VENDORED_DIRS or os.path.islink(os.path.join(current, dirname)):
                found.append(prefix + dirname)
                continue
            descend.append(dirname)
        dirnames[:] = descend  # os.walk reads this list to decide where to go next
        found.extend(prefix + filename for filename in filenames)
    return sorted(found)


def list_repo_files(root: Path) -> List[str]:
    tracked = gitinfo.list_files(root) if gitinfo.is_git_repo(root) else None
    if tracked is None:
        return walk_files(root)
    return [rel for rel in tracked if os.path.lexists(root / rel)]


def build_inventory(root: Path) -> dict:
    """`root` must already be an absolute, resolved directory path."""
    in_git = gitinfo.is_git_repo(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "repo": {
            "name": safe_name(root.name),
            "root": str(root),
            "commit": gitinfo.head_commit(root) if in_git else None,
            "dirty": gitinfo.is_dirty(root) if in_git else None,
        },
        "files": [classify(root, rel) for rel in list_repo_files(root)],
    }
