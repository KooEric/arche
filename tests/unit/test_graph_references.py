from test_graph_multilang import _graph

SOURCES = {
    "dags/a/dag.py": (
        'import os\n'
        'LOAD = "sql/load.sql"\n'
        'AGAIN = "sql/load.sql"; SAME = "sql/load.sql"\n'
        'GONE = "sql/gone.sql"\n'
        'STAGE = f"sql/{name}.sql"\n'
    ),
    "dags/a/sql/load.sql": "select 1\n",
}


def _references(graph):
    return [edge for edge in graph["edges"] if edge["kind"] == "REFERENCES"]


def test_a_path_in_python_text_becomes_a_reference_edge_with_evidence():
    assert _references(_graph(SOURCES))[0] == {
        "kind": "REFERENCES", "from": "file:dags/a/dag.py", "to": "file:dags/a/sql/load.sql",
        "origin": "script", "status": "verified", "rule": "relative",
        "evidence": {"path": "dags/a/dag.py", "line": 2, "quote": 'LOAD = "sql/load.sql"'}}


def test_one_edge_per_line_and_target_and_unresolved_paths_keep_their_reason():
    edges = _references(_graph(SOURCES))

    assert [(e["evidence"]["line"], e["to"], e.get("spec"), e.get("reason")) for e in edges] == [
        (2, "file:dags/a/sql/load.sql", None, None), (3, "file:dags/a/sql/load.sql", None, None),
        (4, None, "sql/gone.sql", "not_found"), (5, None, "sql/{}.sql", "dynamic")]
    assert all("rule" not in e for e in edges if e["to"] is None)


def test_several_matching_files_are_kept_as_candidates():
    graph = _graph({"m.py": 'Q = "x.sql"\n', "q/x.sql": "select 1\n", "r/x.sql": "select 2\n"})

    assert [(e["status"], e["reason"], e["candidates"]) for e in _references(graph)] == [
        ("unresolved", "ambiguous", ["q/x.sql", "r/x.sql"])]


def test_a_skipped_sql_file_is_not_a_target():
    big = {"path": "dags/a/sql/big.sql", "status": "skipped", "reason": "too_large"}
    graph = _graph({"dags/a/dag.py": 'BIG = "sql/big.sql"\n'}, extra_files=[big])

    assert [(e["to"], e["reason"]) for e in _references(graph)] == [(None, "not_found")]


def test_reference_edges_leave_imports_and_externals_as_they_were():
    graph = _graph(SOURCES)

    assert {e["kind"] for e in graph["edges"]} == {"REFERENCES"}
    assert graph["externals"] == [{"from": "file:dags/a/dag.py", "package": "os", "line": 1}]
