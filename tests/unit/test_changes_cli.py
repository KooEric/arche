import json


def _snapshot(repo, out, run_script):
    for script, args in [("scan.py", [repo, "--out", out]), ("imports.py", ["--out", out])]:
        result = run_script(script, *args)
        assert result.returncode == 0, result.stderr


def test_diff_rename_deleted_and_reverse_impact(make_repo, git_cmd, run_script, tmp_path):
    repo = make_repo({"old.py": "value = 1\n", "user.py": "import old\n",
                      "top.py": "import user\n", "gone.py": "x = 1\n"})
    base = git_cmd(repo, "rev-parse", "HEAD").strip()
    out = tmp_path / "run"
    _snapshot(repo, out, run_script)
    git_cmd(repo, "mv", "old.py", "new.py")
    (repo / "gone.py").unlink()
    (repo / "added.py").write_text("different = 'new content'\n")
    git_cmd(repo, "add", "-A")
    git_cmd(repo, "commit", "-qm", "changes")
    head = git_cmd(repo, "rev-parse", "HEAD").strip()
    result = run_script("changes.py", "--out", out, "--base", base, "--head", head)
    assert result.returncode == 0, result.stderr
    changes = json.loads((out / "changes.json").read_text())
    assert changes["impact_available"] is True
    files = {item["path"]: item for item in changes["files"]}
    assert files["new.py"] == {"status": "renamed", "path": "new.py", "old_path": "old.py", "impact": ["top.py", "user.py"]}
    assert files["gone.py"]["status"] == "deleted"
    assert files["added.py"]["impact"] == []
    first = (out / "changes.json").read_bytes()
    assert run_script("changes.py", "--out", out, "--base", base, "--head", head).returncode == 0
    assert (out / "changes.json").read_bytes() == first


def test_unmatched_or_dirty_graph_has_no_impact(make_repo, git_cmd, run_script, tmp_path):
    repo = make_repo({"a.py": "x = 1\n", "b.py": "import a\n"})
    base = git_cmd(repo, "rev-parse", "HEAD").strip()
    (repo / "a.py").write_text("x = 2\n")
    out = tmp_path / "run"
    _snapshot(repo, out, run_script)
    git_cmd(repo, "add", "-A")
    git_cmd(repo, "commit", "-qm", "change")
    result = run_script("changes.py", "--out", out, "--base", base, "--head", "HEAD")
    assert result.returncode == 0, result.stderr
    changes = json.loads((out / "changes.json").read_text())
    assert not changes["impact_available"]
    assert changes["files"][0]["impact"] == []
    assert run_script("changes.py", "--out", out, "--base", "missing", "--head", "HEAD").returncode != 0


def test_changing_a_sql_file_reaches_the_python_file_that_names_it(make_repo, git_cmd, run_script, tmp_path):
    repo = make_repo({"dag.py": 'QUERY = "q/load.sql"\n', "q/load.sql": "select 1\n"})
    base = git_cmd(repo, "rev-parse", "HEAD").strip()
    out = tmp_path / "run"
    _snapshot(repo, out, run_script)
    (repo / "q" / "load.sql").write_text("select 2\n")
    git_cmd(repo, "add", "-A")
    git_cmd(repo, "commit", "-qm", "edit")
    head = git_cmd(repo, "rev-parse", "HEAD").strip()

    result = run_script("changes.py", "--out", out, "--base", base, "--head", head)

    assert result.returncode == 0, result.stderr
    files = {item["path"]: item for item in json.loads((out / "changes.json").read_text())["files"]}
    assert files["q/load.sql"]["impact"] == ["dag.py"]


def test_changing_a_sql_file_reaches_readers_of_its_table_and_the_code_naming_them(make_repo, git_cmd, run_script, tmp_path):
    repo = make_repo({"dag.py": 'QUERY = "q/report.sql"\n', "q/load.sql": "INSERT INTO a.t SELECT 1;\n",
                      "q/report.sql": "SELECT * FROM a.t;\n"})
    base = git_cmd(repo, "rev-parse", "HEAD").strip()
    out = tmp_path / "run"
    _snapshot(repo, out, run_script)
    (repo / "q" / "load.sql").write_text("INSERT INTO a.t SELECT 2;\n")
    git_cmd(repo, "add", "-A")
    git_cmd(repo, "commit", "-qm", "edit")
    head = git_cmd(repo, "rev-parse", "HEAD").strip()

    result = run_script("changes.py", "--out", out, "--base", base, "--head", head)

    assert result.returncode == 0, result.stderr
    files = {item["path"]: item for item in json.loads((out / "changes.json").read_text())["files"]}
    assert files["q/load.sql"]["impact"] == ["dag.py", "q/report.sql"]
