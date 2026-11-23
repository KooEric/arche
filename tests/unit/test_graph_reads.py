from test_graph_multilang import _graph

SOURCES = {
    "q/load.sql": "INSERT INTO a.orders SELECT * FROM staging.orders;\n",
    "q/more.sql": "CREATE TEMPORARY TABLE tmp AS SELECT * FROM a.orders;\nINSERT INTO a.orders SELECT * FROM tmp;\n",
    "q/report.sql": "SELECT *\nFROM a.orders o\nJOIN {{ params.schema }}.orders p ON 1=1\nJOIN a.daily_{{ ds }} d ON 1=1;\n",
}


def _reads(graph):
    return [edge for edge in graph["edges"] if edge["kind"] == "READS"]


def test_a_table_read_becomes_an_edge_to_every_file_that_writes_it():
    edges = [e for e in _reads(_graph(SOURCES)) if e["from"] == "file:q/report.sql" and e["status"] == "verified"]

    assert edges[0] == {
        "kind": "READS", "from": "file:q/report.sql", "to": "file:q/load.sql", "origin": "script",
        "status": "verified", "rule": "name", "table": "a.orders",
        "evidence": {"path": "q/report.sql", "line": 2, "quote": "FROM a.orders o"}}
    assert [e["to"] for e in edges] == ["file:q/load.sql", "file:q/more.sql"]


def test_variable_schema_is_inferred_and_dynamic_names_stay_unresolved():
    edges = [e for e in _reads(_graph(SOURCES)) if e["from"] == "file:q/report.sql" and e["status"] != "verified"]

    assert [(e["evidence"]["line"], e["status"], e["to"], e["table"], e.get("reason")) for e in edges] == [
        (3, "inferred", "file:q/load.sql", "{}.orders", None), (3, "inferred", "file:q/more.sql", "{}.orders", None),
        (4, "unresolved", None, "a.daily_{}", "dynamic")]
    assert all("rule" not in e and e["spec"] == e["table"] for e in edges if e["to"] is None)


def test_a_file_does_not_read_from_itself_or_from_its_own_temporary_table():
    edges = [e for e in _reads(_graph(SOURCES)) if e["from"] == "file:q/more.sql"]

    assert [(e["to"], e["table"]) for e in edges] == [("file:q/load.sql", "a.orders")]


def test_tables_nothing_writes_are_listed_as_external():
    assert _graph(SOURCES)["external_tables"] == [{"from": "file:q/load.sql", "table": "staging.orders", "line": 1}]


def test_reads_leave_other_edges_and_non_sql_repositories_alone():
    graph = _graph({"app/main.py": "import os\n"})

    assert graph["edges"] == [] and graph["external_tables"] == []
