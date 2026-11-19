import json

from arche_lib import gitinfo

FILES = {
    "pkg/__init__.py": "",
    "pkg/a.py": "from pkg import b\nimport json\n",
    "pkg/b.py": "VALUE = 1\n",
}


def test_imports_writes_graph_and_prints_a_summary(make_repo, run_script, tmp_path):
    repo = make_repo(FILES)
    out = tmp_path / "out"
    assert run_script("scan.py", repo, "--out", out).returncode == 0

    done = run_script("imports.py", "--out", out)

    assert done.returncode == 0, done.stderr
    summary = json.loads(done.stdout)
    assert summary["nodes"] == {"file": 3, "symbol": 1}
    assert summary["edges"] == {"verified": 1}
    assert summary["externals"] == 1
    graph = json.loads((out / "graph.json").read_text(encoding="utf-8"))
    assert graph["edges"][0]["to"] == "file:pkg/b.py"
    run = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert sorted(run["steps"]) == ["imports", "scan"]


def test_graph_json_is_byte_identical_when_run_twice(make_repo, run_script, tmp_path):
    repo = make_repo(FILES)
    first, second = tmp_path / "one", tmp_path / "two"
    for out in (first, second):
        assert run_script("scan.py", repo, "--out", out).returncode == 0
        assert run_script("imports.py", "--out", out).returncode == 0

    assert (first / "graph.json").read_bytes() == (second / "graph.json").read_bytes()


def test_imports_leaves_the_repository_untouched(make_repo, run_script, tmp_path):
    repo = make_repo(FILES)
    before = gitinfo.status_hash(repo)
    out = tmp_path / "out"
    run_script("scan.py", repo, "--out", out)

    done = run_script("imports.py", "--out", out)

    assert done.returncode == 0, done.stderr
    assert gitinfo.status_hash(repo) == before
    assert not list(repo.rglob("__pycache__"))


def test_imports_stops_when_a_file_changed_after_the_scan(make_repo, run_script, tmp_path):
    repo = make_repo(FILES)
    out = tmp_path / "out"
    run_script("scan.py", repo, "--out", out)
    (repo / "pkg" / "b.py").write_text("VALUE = 2\n", encoding="utf-8")

    done = run_script("imports.py", "--out", out)

    assert done.returncode == 3
    assert "file changed since scan: pkg/b.py" in done.stderr
    assert not (out / "graph.json").exists()


def test_imports_without_an_inventory_is_an_integrity_error(run_script, tmp_path):
    done = run_script("imports.py", "--out", tmp_path)

    assert done.returncode == 3
    assert "missing input file" in done.stderr
