"""1.3.2 column lineage options: --no-columns, --max-column-edges, and the compact columns.json."""
import json

from arche_lib.jsonio import write_json_rows
from arche_lib.report_columns import ColumnLineage, impact

_SQL = {"q/load.sql": "INSERT INTO s.t SELECT r.a AS b, r.c FROM raw.r AS r;\n",
        "q/mart.sql": "INSERT INTO m.v SELECT t.b AS total, t.c FROM s.t AS t WHERE t.c > 1;\n"}


def _run(run_script, repo, out, *options):
    assert run_script("scan.py", repo, "--out", out).returncode == 0
    done = run_script("imports.py", "--out", out, *options)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_rows_are_one_per_line_and_load_back(tmp_path):
    data = {"b": [{"z": 1, "a": "한"}, {"x": None}], "a": 1, "c": [], "d": {"k": [1]}}
    path = tmp_path / "x.json"

    write_json_rows(path, data)

    text = path.read_text(encoding="utf-8")
    assert json.loads(text) == data
    assert text == '{\n"a":1,\n"b":[\n{"a":"한","z":1},\n{"x":null}\n],\n"c":[],\n"d":{"k":[1]}\n}\n'


def test_rows_past_one_write_chunk_give_the_same_bytes(tmp_path, monkeypatch):
    data = {"rows": [{"i": i} for i in range(10)]}
    whole, chunked = tmp_path / "whole.json", tmp_path / "chunked.json"
    write_json_rows(whole, data)
    monkeypatch.setattr("arche_lib.jsonio._CHUNKS_PER_WRITE", 3)

    write_json_rows(chunked, data)

    assert chunked.read_bytes() == whole.read_bytes()
    assert json.loads(chunked.read_text(encoding="utf-8")) == data


def test_no_columns_writes_a_disabled_document_and_the_same_graph(make_repo, run_script, tmp_path):
    repo = make_repo(_SQL)
    on, off = tmp_path / "on", tmp_path / "off"
    _run(run_script, repo, on)

    summary = _run(run_script, repo, off, "--no-columns")

    assert summary["columns"] == {"disabled": True}
    columns = json.loads((off / "columns.json").read_text(encoding="utf-8"))
    assert columns["disabled"] is True
    assert (columns["columns"], columns["edges"], columns["statements"]) == ([], [], [])
    assert (on / "graph.json").read_bytes() == (off / "graph.json").read_bytes()


def test_max_column_edges_caps_and_records_the_limit(make_repo, run_script, tmp_path):
    out = tmp_path / "out"

    summary = _run(run_script, make_repo(_SQL), out, "--max-column-edges", "2")

    columns = json.loads((out / "columns.json").read_text(encoding="utf-8"))
    assert len(columns["edges"]) == 2
    assert columns["truncated"] is True and summary["columns"]["truncated"] is True
    assert columns["max_edges"] == 2


def test_the_default_limit_is_not_recorded(make_repo, run_script, tmp_path):
    out = tmp_path / "out"

    summary = _run(run_script, make_repo(_SQL), out)

    columns = json.loads((out / "columns.json").read_text(encoding="utf-8"))
    assert "max_edges" not in columns and summary["columns"]["truncated"] is False


def test_column_options_are_checked(make_repo, run_script, tmp_path):
    repo = make_repo(_SQL)
    out = tmp_path / "out"
    assert run_script("scan.py", repo, "--out", out).returncode == 0

    for options, message in ((["--max-column-edges", "0"], "at least 1"),
                             (["--no-columns", "--max-column-edges", "5"], "cannot be combined")):
        done = run_script("imports.py", "--out", out, *options)
        assert done.returncode != 0 and message in done.stderr


def test_arche_passes_no_columns_through(make_repo, run_script, tmp_path):
    out = tmp_path / "out"

    done = run_script("arche.py", make_repo(_SQL), "--out", out, "--no-columns")

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["columns"] == {"disabled": True}
    report = (out / "report.html").read_text(encoding="utf-8")
    assert "--no-columns" in report


def test_changes_marks_column_impact_unavailable_when_columns_were_off(make_repo, git_cmd, run_script, tmp_path):
    repo = make_repo(_SQL)
    base = git_cmd(repo, "rev-parse", "HEAD").strip()
    (repo / "q" / "mart.sql").write_text("INSERT INTO m.v SELECT t.b AS total FROM s.t AS t;\n")
    git_cmd(repo, "add", "-A")
    git_cmd(repo, "commit", "-qm", "edit")
    head = git_cmd(repo, "rev-parse", "HEAD").strip()
    out = tmp_path / "run"
    _run(run_script, repo, out, "--no-columns")

    done = run_script("changes.py", "--out", out, "--base", base, "--head", head)

    assert done.returncode == 0, done.stderr
    column_impact = json.loads((out / "changes.json").read_text())["column_impact"]
    assert (column_impact["available"], column_impact["reason"]) == (False, "columns_disabled")
    assert "--no-columns" in impact(column_impact)


def test_a_truncated_report_names_the_option_that_keeps_more():
    doc = {"columns": [], "edges": [], "unresolved": [], "statements": [], "truncated": True}

    assert "--max-column-edges" in ColumnLineage(doc).section()
