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


_LOAD = "INSERT INTO s.t SELECT r.a AS b, r.c FROM raw.r AS r;\n"
_MART = "INSERT INTO m.v SELECT t.b AS total FROM s.t AS t WHERE t.c > 1;\n"


def _commit_all(repo, git_cmd, message):
    git_cmd(repo, "add", "-A")
    git_cmd(repo, "commit", "-qm", message)
    return git_cmd(repo, "rev-parse", "HEAD").strip()


def test_column_impact_from_one_snapshot_seeds_the_columns_of_changed_files(make_repo, git_cmd, run_script, tmp_path):
    repo = make_repo({"q/load.sql": _LOAD, "q/mart.sql": _MART})
    base = git_cmd(repo, "rev-parse", "HEAD").strip()
    (repo / "q" / "load.sql").write_text(_LOAD.replace("r.a AS b", "r.a + 1 AS b"))
    head = _commit_all(repo, git_cmd, "edit")
    out = tmp_path / "run"
    _snapshot(repo, out, run_script)

    result = run_script("changes.py", "--out", out, "--base", base, "--head", head)

    assert result.returncode == 0, result.stderr
    impact = json.loads((out / "changes.json").read_text())["column_impact"]
    assert impact["available"] is True
    assert [(s["column"], s["seed"], s["path"]) for s in impact["seeds"]] == [
        ("col:s.t.b", "file", "q/load.sql"), ("col:s.t.c", "file", "q/load.sql")]
    assert impact["affected"] == [{"column": "col:m.v.total", "depth": 1, "kinds": ["rename"],
                                   "status": "inferred", "files": ["q/mart.sql"]}]
    assert [(u["table"], u["kinds"]) for u in impact["used_by"]] == [("table:m.v", ["filter"])]
    assert impact["affected_tables"] == ["m.v"]
    assert impact["affected_files"] == ["q/mart.sql"]
    first = (out / "changes.json").read_bytes()
    assert run_script("changes.py", "--out", out, "--base", base, "--head", head).returncode == 0
    assert (out / "changes.json").read_bytes() == first


def test_column_impact_from_two_snapshots_walks_removed_columns_on_the_base(make_repo, git_cmd, run_script, tmp_path):
    repo = make_repo({"q/load.sql": _LOAD, "q/mart.sql": _MART})
    base = git_cmd(repo, "rev-parse", "HEAD").strip()
    base_out = tmp_path / "base"
    _snapshot(repo, base_out, run_script)
    (repo / "q" / "load.sql").write_text(_LOAD.replace("r.c", "r.c AS d"))
    head = _commit_all(repo, git_cmd, "rename c")
    out = tmp_path / "run"
    _snapshot(repo, out, run_script)

    result = run_script("changes.py", "--out", out, "--base", base, "--head", head, "--base-out", base_out)

    assert result.returncode == 0, result.stderr
    impact = json.loads((out / "changes.json").read_text())["column_impact"]
    assert [(s["column"], s["change"], s["seed"]) for s in impact["seeds"]] == [
        ("col:s.t.c", "removed", "column_diff"), ("col:s.t.d", "added", "column_diff")]
    assert impact["affected"] == []
    assert [(u["table"], u["kinds"], u["files"]) for u in impact["used_by"]] == [
        ("table:m.v", ["filter"], ["q/mart.sql"])]


def test_no_columns_json_leaves_changes_output_without_column_impact(make_repo, git_cmd, run_script, tmp_path):
    repo = make_repo({"a.py": "x = 1\n", "b.py": "import a\n"})
    base = git_cmd(repo, "rev-parse", "HEAD").strip()
    (repo / "a.py").write_text("x = 2\n")
    head = _commit_all(repo, git_cmd, "edit")
    out = tmp_path / "run"
    _snapshot(repo, out, run_script)
    assert not (out / "columns.json").exists()

    result = run_script("changes.py", "--out", out, "--base", base, "--head", head)

    assert result.returncode == 0, result.stderr
    changes = json.loads((out / "changes.json").read_text())
    assert sorted(changes) == ["base", "files", "graph_commit", "head", "impact_available", "schema_version"]


def test_columns_json_of_another_snapshot_is_rejected(make_repo, git_cmd, run_script, tmp_path):
    repo = make_repo({"q/load.sql": _LOAD, "q/mart.sql": _MART})
    base = git_cmd(repo, "rev-parse", "HEAD").strip()
    (repo / "q" / "mart.sql").write_text(_MART.replace("> 1", "> 2"))
    head = _commit_all(repo, git_cmd, "edit")
    out = tmp_path / "run"
    _snapshot(repo, out, run_script)
    columns = json.loads((out / "columns.json").read_text())
    (out / "columns.json").write_text(json.dumps({**columns, "inventory_sha256": "0" * 64}))

    result = run_script("changes.py", "--out", out, "--base", base, "--head", head)

    assert result.returncode != 0
    assert "columns.json describes a different snapshot" in result.stderr
    assert not (out / "changes.json").exists()


def test_column_impact_is_unavailable_when_the_graph_matches_neither_commit(make_repo, git_cmd, run_script, tmp_path):
    repo = make_repo({"q/load.sql": _LOAD, "q/mart.sql": _MART})
    base = git_cmd(repo, "rev-parse", "HEAD").strip()
    (repo / "q" / "load.sql").write_text(_LOAD.replace("r.c", "r.c AS d"))
    out = tmp_path / "run"
    _snapshot(repo, out, run_script)
    head = _commit_all(repo, git_cmd, "edit")

    result = run_script("changes.py", "--out", out, "--base", base, "--head", head)

    assert result.returncode == 0, result.stderr
    impact = json.loads((out / "changes.json").read_text())["column_impact"]
    assert impact == {"available": False, "seeds": [], "affected": [], "used_by": [], "affected_files": [],
                      "affected_tables": [], "truncated": False}
