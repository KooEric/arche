"""Read-only git queries. Never runs hooks or programs named by repo config."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import List, Optional

GIT_TIMEOUT_SECONDS = 60
SUBJECT_LIMIT = 120

# A hostile repository can set core.fsmonitor to a command that `git status`
# would execute. Override it on every call, and pass --no-optional-locks so
# that asking for status never rewrites the index inside .git.
_SAFE_PREFIX = (
    "git",
    "--no-optional-locks",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
)


def _git(root: Path, *args: str) -> Optional[bytes]:
    """Run a git query in `root`. None means git failed or is unavailable."""
    command = [*_SAFE_PREFIX, "-C", str(root), *args]
    try:
        done = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def is_git_repo(root: Path) -> bool:
    out = _git(root, "rev-parse", "--is-inside-work-tree")
    return out is not None and out.strip() == b"true"


def head_commit(root: Path) -> Optional[str]:
    out = _git(root, "rev-parse", "--verify", "HEAD")
    return out.decode("ascii").strip() if out else None


def list_files(root: Path) -> Optional[List[str]]:
    """Tracked files plus untracked files that are not ignored, sorted."""
    out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if out is None:
        return None
    names = {raw.decode("utf-8", errors="replace") for raw in out.split(b"\0") if raw}
    return sorted(names)


def status_bytes(root: Path) -> Optional[bytes]:
    return _git(root, "status", "--porcelain=v1", "-z")


def status_hash(root: Path) -> Optional[str]:
    """Fingerprint of the working-tree state, compared before and after a run."""
    status = status_bytes(root)
    return None if status is None else hashlib.sha256(status).hexdigest()


def is_dirty(root: Path) -> Optional[bool]:
    status = status_bytes(root)
    return None if status is None else bool(status)


def recent_commits(root: Path, commit: str) -> Optional[List[dict]]:
    """Return up to 12 commits reachable from the scanned revision."""
    out = _git(
        root, "log", "--no-show-signature", "--no-notes", "--no-decorate",
        "--max-count=12", "--format=%H %cI %s", commit, "--", ".",
    )
    if out is None:
        return None
    commits = []
    for line in out.decode("utf-8", errors="replace").splitlines():
        revision, date, *subject = line.split(" ", 2)
        commits.append({"commit": revision, "date": date, "subject": subject[0][:SUBJECT_LIMIT] if subject else ""})
    return commits
