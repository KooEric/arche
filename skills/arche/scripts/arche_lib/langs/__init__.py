"""Language detection by file extension."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Optional

LANG_BY_EXT = {
    ".py": "python",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".dart": "dart", ".go": "go",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".sql": "sql", ".hql": "sql",
    ".jsonnet": "jsonnet", ".libsonnet": "jsonnet",
    ".tf": "terraform",
}


def language_for(rel_path: str) -> Optional[str]:
    """Language name for a path, or None when no adapter covers it."""
    return LANG_BY_EXT.get(PurePosixPath(rel_path).suffix.lower())
