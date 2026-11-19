import os

from arche_lib import paths


def test_is_within_accepts_root_and_descendants(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)

    assert paths.is_within(tmp_path, tmp_path) is True
    assert paths.is_within(tmp_path, tmp_path / "a" / "b") is True


def test_is_within_rejects_siblings_with_a_shared_prefix(tmp_path):
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo-other").mkdir()

    assert paths.is_within(tmp_path / "repo", tmp_path / "repo-other") is False


def test_is_within_rejects_a_symlink_that_escapes_the_root(tmp_path):
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    os.symlink(outside, root / "link")

    assert paths.is_within(root, root / "link") is False


def test_safe_name_replaces_unsafe_characters_and_never_returns_empty():
    assert paths.safe_name("my repo (v2)") == "my_repo_v2"
    assert paths.safe_name("한글") == "repo"
    assert paths.safe_name("demo") == "demo"


def test_default_out_dir_uses_short_commit_and_dirty_suffix(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHE_HOME", str(tmp_path))
    commit = "3f2a91c0d4e5f60718293a4b5c6d7e8f90a1b2c3"

    assert paths.default_out_dir("demo", commit, False) == tmp_path / "runs" / "demo-3f2a91c"
    assert paths.default_out_dir("demo", commit, True) == tmp_path / "runs" / "demo-3f2a91c-dirty"


def test_default_out_dir_without_git_uses_nogit_label(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHE_HOME", str(tmp_path))

    assert paths.default_out_dir("notes", None, None) == tmp_path / "runs" / "notes-nogit"
