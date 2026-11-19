import json

from arche_lib import gitinfo


def test_scan_writes_inventory_and_run_log_and_prints_a_summary(make_repo, run_script, tmp_path):
    repo = make_repo({"a.py": "x = 1\n", "README.md": "# hi\n", ".env": "K=v\n"})
    out = tmp_path / "out"

    done = run_script("scan.py", repo, "--out", out)

    assert done.returncode == 0, done.stderr
    summary = json.loads(done.stdout)
    assert summary["step"] == "scan"
    assert summary["files"] == 3
    assert summary["analyzed"] == 1
    assert summary["skipped"] == {"secret": 1, "unsupported_lang": 1}
    inventory = json.loads((out / "inventory.json").read_text(encoding="utf-8"))
    assert inventory["repo"]["root"] == str(repo)
    run = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert run["status_before"] == gitinfo.status_hash(repo)
    assert run["out_inside_repo"] is False
    assert "scan" in run["steps"]


def test_scan_leaves_the_repository_untouched(make_repo, run_script, tmp_path):
    repo = make_repo({"a.py": "x = 1\n"})
    before = gitinfo.status_hash(repo)

    done = run_script("scan.py", repo, "--out", tmp_path / "out")

    assert done.returncode == 0, done.stderr
    assert gitinfo.status_hash(repo) == before


def test_scan_default_output_goes_under_arche_home(make_repo, run_script, tmp_path):
    repo = make_repo({"a.py": "x = 1\n"}, use_git=False)
    home = tmp_path / "home"

    done = run_script("scan.py", repo, env={"ARCHE_HOME": str(home)})

    assert done.returncode == 0, done.stderr
    assert (home / "runs" / "repo-nogit" / "inventory.json").is_file()


def test_scan_records_when_output_is_inside_the_repository(make_repo, run_script):
    repo = make_repo({"a.py": "x = 1\n"}, use_git=False)

    done = run_script("scan.py", repo, "--out", repo / ".arche-out")

    assert done.returncode == 0, done.stderr
    run = json.loads((repo / ".arche-out" / "run.json").read_text(encoding="utf-8"))
    assert run["out_inside_repo"] is True


def test_scan_rejects_a_path_that_is_not_a_directory(run_script, tmp_path):
    done = run_script("scan.py", tmp_path / "missing", "--out", tmp_path / "out")

    assert done.returncode == 2
    assert "not a directory" in done.stderr
    assert done.stdout == ""
