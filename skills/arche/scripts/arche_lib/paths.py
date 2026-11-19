"""Path helpers: containment checks and the default output directory."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

ARCHE_HOME_ENV = "ARCHE_HOME"
SHORT_COMMIT_LENGTH = 7
NO_GIT_LABEL = "nogit"
FALLBACK_REPO_NAME = "repo"
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def is_within(root: Path, candidate: Path) -> bool:
    """True when `candidate` resolves to `root` or to something inside it."""
    return Path(os.path.realpath(candidate)).is_relative_to(os.path.realpath(root))


def safe_name(name: str) -> str:
    """Reduce a directory name to characters that are safe in a folder name."""
    cleaned = _UNSAFE_NAME_CHARS.sub("_", name).strip("._")
    return cleaned or FALLBACK_REPO_NAME


def arche_home() -> Path:
    override = os.environ.get(ARCHE_HOME_ENV)
    return Path(override) if override else Path.home() / ".arche"


def default_out_dir(repo_name: str, commit: Optional[str], dirty: Optional[bool]) -> Path:
    label = commit[:SHORT_COMMIT_LENGTH] if commit else NO_GIT_LABEL
    suffix = "-dirty" if dirty else ""
    return arche_home() / "runs" / f"{repo_name}-{label}{suffix}"
