"""Shared fixtures: throwaway repositories and a runner for the skill's scripts."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "arche" / "scripts"
_GIT_IDENTITY = (
    "-c", "user.name=arche-test",
    "-c", "user.email=arche-test@example.com",
    "-c", "commit.gpgsign=false",
)


@pytest.fixture(autouse=True)
def isolated_git_config(monkeypatch):
    """Keep the developer's global git config (ignores, hooks) out of the tests."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def git_cmd():
    def _git(root, *args):
        done = subprocess.run(
            ["git", *_GIT_IDENTITY, "-C", str(root), *args],
            check=True, capture_output=True, text=True,
        )
        return done.stdout

    return _git


@pytest.fixture
def make_repo(tmp_path, git_cmd):
    """make_repo(files, use_git=True, commit=True) -> resolved Path of a new repo."""

    def _make(files, name="repo", use_git=True, commit=True):
        root = Path(os.path.realpath(tmp_path)) / name
        root.mkdir()
        for rel, content in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")
        if use_git:
            git_cmd(root, "init", "-q")
            if commit:
                git_cmd(root, "add", "-A")
                git_cmd(root, "commit", "-q", "-m", "init")
        return root

    return _make


@pytest.fixture
def run_script():
    """run_script("scan.py", arg, ..., env={...}) -> CompletedProcess with text output."""

    def _run(name, *args, env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / name), *[str(arg) for arg in args]],
            capture_output=True, text=True, env={**os.environ, **(env or {})},
        )

    return _run
