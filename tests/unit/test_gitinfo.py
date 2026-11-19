from arche_lib import gitinfo


def test_plain_directory_is_not_a_git_repo(tmp_path):
    assert gitinfo.is_git_repo(tmp_path) is False
    assert gitinfo.list_files(tmp_path) is None
    assert gitinfo.status_hash(tmp_path) is None
    assert gitinfo.is_dirty(tmp_path) is None


def test_list_files_returns_tracked_and_untracked_but_not_ignored(make_repo):
    repo = make_repo({".gitignore": "ignored.txt\n", "a.py": "x = 1\n"})
    (repo / "new.py").write_text("y = 2\n", encoding="utf-8")
    (repo / "ignored.txt").write_text("skip me\n", encoding="utf-8")

    assert gitinfo.list_files(repo) == [".gitignore", "a.py", "new.py"]


def test_head_commit_and_dirty_state_follow_the_working_tree(make_repo):
    repo = make_repo({"a.py": "x = 1\n"})
    clean_hash = gitinfo.status_hash(repo)

    assert len(gitinfo.head_commit(repo)) == 40
    assert gitinfo.is_dirty(repo) is False

    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")

    assert gitinfo.is_dirty(repo) is True
    assert gitinfo.status_hash(repo) != clean_hash


def test_repo_without_commits_has_no_head(make_repo):
    repo = make_repo({"a.py": "x = 1\n"}, commit=False)

    assert gitinfo.is_git_repo(repo) is True
    assert gitinfo.head_commit(repo) is None


def test_queries_do_not_run_a_repo_configured_fsmonitor(make_repo, git_cmd, tmp_path):
    repo = make_repo({"a.py": "x = 1\n"})
    marker = tmp_path / "fsmonitor-ran"
    hook = tmp_path / "hook.sh"
    hook.write_text("#!/bin/sh\ntouch '%s'\n" % marker, encoding="utf-8")
    hook.chmod(0o755)
    git_cmd(repo, "config", "core.fsmonitor", str(hook))

    gitinfo.status_hash(repo)
    gitinfo.list_files(repo)

    assert not marker.exists()
