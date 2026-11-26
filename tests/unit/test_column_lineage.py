"""columns.json end to end: graph.build_graph_and_columns and the imports.py CLI."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from arche_lib import lineage
from arche_lib.column_lineage import parse_file
from arche_lib.graph import build_graph, build_graph_and_columns
from arche_lib.inventory import count_lines
from arche_lib.langs import language_for

REPO_ROOT = Path(__file__).resolve().parents[2]
DBT_DOCS = REPO_ROOT / "evals" / "fixtures" / "dbt-docs" / "repo"


def _inventory(sources):
    files = [{"path": path, "status": "analyzed", "lang": language_for(path), "bytes": len(text),
              "lines": count_lines(text), "sha256": hashlib.sha256(text.encode()).hexdigest()}
             for path, text in sources.items() if language_for(path)]
    return {"schema_version": "0.1", "repo": {"name": "demo", "root": "/abs/demo", "commit": None, "dirty": None},
            "files": files}


def _both(sources):
    return build_graph_and_columns(_inventory(sources), sources.get, {})


def _columns(sources):
    return _both(sources)[1]


def _edges(doc, **match):
    return [(e["from"], e["to"], e["kind"], e["status"], e["rule"]) for e in doc["edges"]
            if all(e.get(key) == value for key, value in match.items())]


def _edge(doc, source, target):
    (found,) = [e for e in doc["edges"] if e["from"] == source and e["to"] == target]
    return found


def _scan_and_import(run_script, repo, out):
    assert run_script("scan.py", repo, "--out", out).returncode == 0
    done = run_script("imports.py", "--out", out)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# -- the document -----------------------------------------------------------------------
def test_a_repository_without_sql_has_no_columns_document():
    sources = {"a.py": "import os\n"}

    graph, columns = _both(sources)

    assert columns is None
    assert json.dumps(graph, sort_keys=True) == json.dumps(build_graph(_inventory(sources), sources.get),
                                                           sort_keys=True)


def test_graph_json_is_the_same_with_and_without_columns():
    sources = {"q/a.sql": "CREATE TABLE s.a AS SELECT id FROM raw.x;\n", "q/b.sql": "SELECT id FROM s.a;\n"}

    graph, _ = _both(sources)

    assert graph == build_graph(_inventory(sources), sources.get)


def test_the_document_has_its_version_and_the_graphs_inventory_digest():
    sources = {"q/a.sql": "SELECT 1 AS one;\n"}

    graph, columns = _both(sources)

    assert columns["columns_version"] == 1
    assert columns["inventory_sha256"] == graph["inventory_sha256"]
    assert columns["truncated"] is False


def test_two_runs_give_the_same_document():
    sources = {"q/a.sql": "CREATE TABLE s.a AS SELECT r.id, r.amt FROM raw.x r WHERE r.k > 0;\n",
               "q/b.sql": "CREATE TABLE s.b AS SELECT * FROM s.a;\nSELECT x FROM s.b JOIN raw.y USING (id);\n"}

    assert json.dumps(_columns(sources)) == json.dumps(_columns(sources))


def test_a_query_without_a_target_writes_a_query_node_with_c_names():
    columns = _columns({"q/a.sql": "SELECT r.id, r.id + 1 FROM raw.x r;\n"})

    assert sorted(c["id"] for c in columns["columns"] if c["table"].startswith("query:")) == [
        "col:query:q/a.sql#0._c1", "col:query:q/a.sql#0.id"]


def test_a_column_records_where_it_is_defined():
    columns = _columns({"q/a.sql": "CREATE TABLE s.a AS\nSELECT r.id\nFROM raw.x r;\n"})

    (defined,) = [c for c in columns["columns"] if c["id"] == "col:s.a.id"]
    assert defined["known_from"] == ["sql_output"]
    assert defined["defined"] == {"path": "q/a.sql", "line": 2, "quote": "SELECT r.id"}


def test_an_external_column_is_marked_external_with_no_known_from():
    columns = _columns({"q/a.sql": "CREATE TABLE s.a AS SELECT r.id FROM raw.x r;\n"})

    (raw,) = [c for c in columns["columns"] if c["id"] == "col:raw.x.id"]
    assert (raw["external"], raw["known_from"]) == (True, [])


def test_an_edge_carries_its_expression_and_evidence():
    columns = _columns({"q/a.sql": "CREATE TABLE s.a AS SELECT r.amt   AS amount FROM raw.x r;\n"})

    edge = _edge(columns, "col:raw.x.amt", "col:s.a.amount")
    assert (edge["kind"], edge["status"], edge["rule"], edge["expression"]) == ("rename", "verified", "qualified",
                                                                                 "r.amt AS amount")
    assert edge["evidence"]["path"] == "q/a.sql" and edge["evidence"]["line"] == 1


def test_a_condition_column_edge_has_the_condition_role():
    columns = _columns({"q/a.sql": "CREATE TABLE s.a AS SELECT CASE WHEN r.k > 0 THEN r.v END AS c FROM raw.x r;\n"})

    assert _edge(columns, "col:raw.x.k", "col:s.a.c")["role"] == "condition"
    assert "role" not in _edge(columns, "col:raw.x.v", "col:s.a.c")


def test_filters_point_at_the_written_table():
    columns = _columns({"q/a.sql": "CREATE TABLE s.a AS SELECT r.id FROM raw.x r WHERE r.k > 0;\n"})

    assert _edges(columns, kind="filter") == [("col:raw.x.k", "table:s.a", "filter", "verified", "qualified")]


def test_edges_are_sorted_by_target_source_and_kind():
    columns = _columns({"q/a.sql": "CREATE TABLE s.b AS SELECT r.z, r.a FROM raw.x r WHERE r.m = 1;\n"
                                   "CREATE TABLE s.a AS SELECT r.y FROM raw.x r;\n"})

    keys = [(e["to"], e["from"] or "", e["kind"]) for e in columns["edges"]]
    assert keys == sorted(keys)


def test_a_literal_column_is_a_constant_edge_from_nothing():
    columns = _columns({"q/a.sql": "CREATE TABLE s.a AS SELECT 'x' AS c;\n"})

    (edge,) = columns["edges"]
    assert (edge["from"], edge["to"], edge["kind"], edge["rule"]) == (None, "col:s.a.c", "constant", "literal")


def test_a_temporary_table_is_not_a_node_and_later_statements_pass_through_it():
    columns = _columns({"q/t.sql": "CREATE TEMP TABLE tt AS SELECT r.a FROM raw.x r;\n"
                                   "INSERT INTO db.o (a) SELECT a FROM tt;\n"})

    assert [(e["from"], e["to"], e["via"]) for e in columns["edges"]] == [("col:raw.x.a", "col:db.o.a", ["temp:tt"])]
    assert not any(c["table"] == "tt" for c in columns["columns"])


# -- across files -----------------------------------------------------------------------
def test_a_star_expands_with_the_columns_another_file_defines():
    columns = _columns({"q/a.sql": "CREATE TABLE s.a AS SELECT id, amt FROM raw.x;\n",
                        "q/b.sql": "CREATE TABLE s.b AS SELECT * FROM s.a;\n"})

    assert _edges(columns, to="col:s.b.amt") == [("col:s.a.amt", "col:s.b.amt", "direct", "inferred", "star")]
    assert _edges(columns, to="col:s.b.id") == [("col:s.a.id", "col:s.b.id", "direct", "inferred", "star")]


def test_a_star_reads_a_file_that_sorts_after_it():
    columns = _columns({"q/b.sql": "CREATE TABLE s.b AS SELECT * FROM s.z;\n",
                        "q/z.sql": "CREATE TABLE s.z AS SELECT r.id FROM raw.x r;\n"})

    assert _edges(columns, to="col:s.b.id") == [("col:s.z.id", "col:s.b.id", "direct", "inferred", "star")]


def test_an_unknown_star_chain_passes_a_read_name_upstream():
    columns = _columns({"q/a.sql": "CREATE TABLE s.a AS SELECT * FROM raw.x;\n",
                        "q/b.sql": "CREATE TABLE s.b AS SELECT * FROM s.a;\n",
                        "q/c.sql": "CREATE TABLE s.c AS SELECT q FROM s.b;\n"})

    assert _edges(columns, rule="star_passthrough") == [
        ("col:raw.x.q", "col:s.a.q", "direct", "inferred", "star_passthrough"),
        ("col:s.a.q", "col:s.b.q", "direct", "inferred", "star_passthrough")]
    assert sorted((u["to"], u["reason"], u["source"]) for u in columns["unresolved"]) == [
        ("table:s.a", "unknown_star", "raw.x"), ("table:s.b", "unknown_star", "s.a")]


def test_an_unknown_star_adds_no_names_nobody_reads():
    columns = _columns({"q/a.sql": "CREATE TABLE s.a AS SELECT * FROM raw.x;\n"})

    assert columns["edges"] == []


def test_a_table_written_by_name_only_lowers_the_status():
    columns = _columns({"q/a.sql": "CREATE TABLE lake.s.a AS SELECT r.id FROM raw.x r;\n",
                        "q/b.sql": "CREATE TABLE s.b AS SELECT t.id FROM s.a t;\n"})

    edge = _edge(columns, "col:lake.s.a.id", "col:s.b.id")
    assert (edge["status"], edge["rule"], edge["table_rule"]) == ("inferred", "table_name", "table_name")


def test_an_insert_without_a_column_list_matches_ddl_by_position():
    columns = _columns({"q/ddl.sql": "CREATE TABLE m.t (id INT, total INT) PARTITIONED BY (dt STRING);\n",
                        "q/load.sql": "INSERT INTO m.t PARTITION (dt='2020') SELECT r.id, r.amt AS amount "
                                      "FROM raw.x r;\n"})

    assert _edge(columns, "col:raw.x.id", "col:m.t.id")["rule"] == "positional"
    mismatch = _edge(columns, "col:raw.x.amt", "col:m.t.total")
    assert (mismatch["status"], mismatch["rule"], mismatch["alias_mismatch"]) == ("inferred", "alias_mismatch", True)
    assert _edges(columns, to="col:m.t.dt") == [(None, "col:m.t.dt", "constant", "verified", "literal")]


def test_ddl_columns_are_listed_with_no_edges():
    columns = _columns({"q/ddl.sql": "CREATE TABLE m.t (id INT, total INT);\n"})

    assert [(c["id"], c["known_from"]) for c in columns["columns"]] == [("col:m.t.id", ["ddl"]),
                                                                        ("col:m.t.total", ["ddl"])]
    assert columns["edges"] == []


def test_an_insert_of_the_wrong_width_is_partial_arity():
    columns = _columns({"q/ddl.sql": "CREATE TABLE m.t (id INT, total INT);\n",
                        "q/load.sql": "INSERT INTO m.t SELECT r.id FROM raw.x r;\n"})

    assert [s["status"] + ":" + s["reason"] for s in columns["statements"] if s["path"] == "q/load.sql"] == [
        "partial:arity"]
    assert ("table:m.t", "arity") in [(u["to"], u["reason"]) for u in columns["unresolved"]]


def test_an_insert_into_an_unlisted_table_matches_by_select_alias():
    columns = _columns({"q/load.sql": "INSERT INTO m.t SELECT r.id FROM raw.x r;\n"})

    edge = _edge(columns, "col:raw.x.id", "col:m.t.id")
    assert (edge["status"], edge["rule"]) == ("inferred", "select_alias")


def test_an_insert_alias_list_is_not_used_for_positions():
    columns = _columns({"q/a.sql": "INSERT INTO m.t SELECT r.id, r.v FROM raw.x r;\n",
                        "q/b.sql": "INSERT INTO m.t SELECT r.v, r.id FROM raw.y r;\n"})

    assert _edge(columns, "col:raw.y.v", "col:m.t.v")["rule"] == "select_alias"


def test_an_unnamed_insert_expression_is_partial_unnamed():
    columns = _columns({"q/load.sql": "INSERT INTO m.t SELECT r.id, r.v + 1 FROM raw.x r;\n"})

    assert [(s["status"], s["reason"]) for s in columns["statements"]] == [("partial", "unnamed")]


def test_sql_output_and_ddl_that_disagree_are_reported():
    columns = _columns({"q/ddl.sql": "CREATE TABLE m.t (id INT, other INT);\n",
                        "q/load.sql": "CREATE TABLE m.t AS SELECT id, amt FROM raw.x;\n"})

    assert columns["column_sources_disagree"] == [{
        "table": "m.t", "chosen": "sql_output", "other": "ddl", "only_chosen": ["amt"], "only_other": ["other"],
        "chosen_at": {"path": "q/load.sql", "index": 0}, "other_at": {"path": "q/ddl.sql", "index": 0}}]


def test_the_first_definition_by_path_wins():
    columns = _columns({"q/a.sql": "CREATE TABLE m.t AS SELECT r.x FROM raw.a r;\n",
                        "q/b.sql": "CREATE TABLE m.t AS SELECT r.y FROM raw.b r;\n",
                        "q/c.sql": "SELECT * FROM m.t;\n"})

    assert [e["from"] for e in columns["edges"] if e["to"].startswith("col:query:q/c.sql")] == ["col:m.t.x"]


def test_merge_writes_each_assigned_column_once():
    columns = _columns({"q/m.sql": "MERGE INTO db.t t USING raw.s s ON t.id = s.id "
                                   "WHEN MATCHED THEN UPDATE SET v = s.v "
                                   "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v);\n"})

    assert _edges(columns, to="col:db.t.v") == [("col:raw.s.v", "col:db.t.v", "direct", "verified", "qualified")]
    assert ("col:raw.s.id", "table:db.t", "join_key", "verified", "qualified") in _edges(columns, kind="join_key")


def test_hive_multi_insert_writes_both_targets():
    columns = _columns({"q/h.hql": "FROM raw.src s INSERT OVERWRITE TABLE a.x SELECT s.k WHERE s.k > 0 "
                                   "INSERT OVERWRITE TABLE a.y SELECT s.v;\n"})

    assert sorted((e["from"], e["to"]) for e in columns["edges"] if e["to"].startswith("col:")) == [
        ("col:raw.src.k", "col:a.x.k"),
        ("col:raw.src.v", "col:a.y.v")]
    assert [s["index"] for s in columns["statements"]] == [0, 1]


def test_a_hive_ctas_with_partitioned_by_keeps_its_select_names():
    columns = _columns({"q/a.sql": "CREATE TABLE db.h PARTITIONED BY (dt) AS SELECT r.a, r.dt FROM raw.x r;\n"})

    assert sorted(e["to"] for e in columns["edges"]) == ["col:db.h.a", "col:db.h.dt"]


def test_a_lateral_view_does_not_stop_the_build():
    columns = _columns({"q/l.sql": "SELECT item FROM raw.x LATERAL VIEW explode(arr) e AS item;\n"})

    assert _edges(columns, rule="table_function")


def test_hive_multi_insert_without_where_between_inserts():
    columns = _columns({"q/h.hql": "FROM raw.src s INSERT OVERWRITE TABLE a.x SELECT s.k "
                                   "INSERT OVERWRITE TABLE a.y SELECT s.v;\n"})

    assert sorted(e["to"] for e in columns["edges"]) == ["col:a.x.k", "col:a.y.v"]


# -- statements -------------------------------------------------------------------------
def test_statement_statuses_are_recorded_and_skipped_statements_are_left_out():
    columns = _columns({"q/a.sql": "SELECT r.a FROM raw.t r extra junk;\nSELECT 1 FROM t PIVOT (x);\n"
                                   "UPDATE t SET a = 1;\nSELECT FROM;\nINSERT INTO ledger.daily_{{ ds }} "
                                   "SELECT 1 AS x;\nSELECT 2 AS y;\n"})

    assert [(s["index"], s["line"], s["status"], s["reason"]) for s in columns["statements"]] == [
        (0, 1, "partial", "tail"), (1, 2, "unsupported", "pivot"), (2, 4, "parse_error", "expected table"),
        (3, 5, "partial", "dynamic"), (4, 6, "parsed", None)]


def test_a_dynamic_target_is_reported_as_unresolved():
    columns = _columns({"q/a.sql": "INSERT INTO ledger.daily_{{ ds }} SELECT r.id FROM raw.x r;\n"})

    assert [(u["to"], u["reason"]) for u in columns["unresolved"]] == [(None, "dynamic")]


def test_a_dynamic_target_spec_stays_inside_its_statement():
    columns = _columns({"q/a.sql": "INSERT INTO a.d_{{ ds }} SELECT 1 AS x;\nSELECT nxt;\n"})

    (entry,) = [u for u in columns["unresolved"] if u["reason"] == "dynamic"]
    assert entry["spec"] == "INSERT INTO a.d_{{ ds }}"


def test_a_too_large_statement_is_recorded():
    found = parse_file("SELECT " + "a, " * 150_000 + "b FROM t;\nSELECT 1;")

    assert [(p.status, p.reason) for p in found] == [("too_large", "too_large"), ("parsed", None)]


def test_a_failed_statement_does_not_stop_the_next():
    found = parse_file("SELECT FROM;\nSELECT a FROM t;")

    assert [p.status for p in found] == ["parse_error", "parsed"]


def test_a_procedural_statement_is_unsupported():
    assert [(p.status, p.reason) for p in parse_file("BEGIN;")] == [("unsupported", "procedural")]


def test_a_table_that_cannot_be_matched_is_reported_once_per_table():
    columns = _columns({"q/x.sql": "CREATE TABLE a.t AS SELECT 1 AS v;\n", "q/y.sql": "CREATE TABLE b.t AS SELECT 1 AS v;\n",
                        "q/z.sql": "SELECT t.v, t.w FROM t WHERE t.k = 1;\n"})

    found = [(u["reason"], u.get("candidates")) for u in columns["unresolved"] if u["evidence"]["path"] == "q/z.sql"]
    assert found == [("table_ambiguous", ["a.t", "b.t"])]


# -- dbt --------------------------------------------------------------------------------
@pytest.fixture
def dbt_docs(tmp_path, run_script):
    repo = tmp_path / "repo"
    shutil.copytree(DBT_DOCS, repo)
    out = tmp_path / "out"
    summary = _scan_and_import(run_script, repo, out)
    return summary, json.loads((out / "columns.json").read_text(encoding="utf-8")), out


def test_a_dbt_ref_edge_is_verified_with_the_dbt_ref_table_rule(dbt_docs):
    _, columns, _ = dbt_docs

    edge = _edge(columns, "col:{}_staging.customers_clean.name", "col:{}_marts.customers.name")
    assert (edge["status"], edge["rule"], edge["table_rule"]) == ("verified", "listed", "dbt_ref")


def test_a_versioned_ref_reads_the_versioned_model(dbt_docs):
    _, columns, _ = dbt_docs

    edge = _edge(columns, "col:{}_marts.orders_v2.amount", "col:{}_finance.revenue.revenue")
    assert (edge["kind"], edge["status"]) == ("aggregate", "verified")


def test_a_dbt_source_reads_the_resolved_table(dbt_docs):
    _, columns, _ = dbt_docs

    edge = _edge(columns, "col:lake.raw_data.customers.id", "col:{}_staging.customers_clean.customer_id")
    assert (edge["kind"], edge["status"], edge["rule"]) == ("rename", "inferred", "single_source")


def test_a_documented_source_column_is_inferred_documented_and_marked(dbt_docs):
    _, columns, _ = dbt_docs

    edge = _edge(columns, "col:lake.raw_data.ev_v2.event_id", "col:{}_staging.stg_events.event_id")
    assert (edge["status"], edge["rule"]) == ("inferred", "documented")
    (column,) = [c for c in columns["columns"] if c["id"] == "col:lake.raw_data.ev_v2.event_id"]
    assert column["known_from"] == ["documented"]


def test_an_ambiguous_dbt_source_is_reported_once(dbt_docs):
    _, columns, _ = dbt_docs

    found = [u for u in columns["unresolved"] if u["reason"] == "table_ambiguous"]
    assert [(u["to"], u["evidence"]["path"]) for u in found] == [
        ("table:{}_staging.stg_orders", "jaffle/models/staging/stg_orders.sql")]


def test_a_dynamic_alias_model_is_named_after_its_file(dbt_docs):
    _, columns, _ = dbt_docs

    assert [s["target"] for s in columns["statements"] if s["path"].endswith("dynamic_alias.sql")] == ["dynamic_alias"]


def test_the_imports_summary_counts_columns(dbt_docs):
    summary, columns, _ = dbt_docs

    assert summary["columns"]["columns"] == len(columns["columns"])
    assert sum(summary["columns"]["edges"].values()) == len(columns["edges"])
    assert summary["columns"]["unresolved"] == len(columns["unresolved"])
    assert summary["columns"]["statements"] == {"parsed": len(columns["statements"])}


def test_the_columns_digest_matches_graph_json(dbt_docs):
    _, columns, out = dbt_docs

    graph = json.loads((out / "graph.json").read_text(encoding="utf-8"))
    assert columns["inventory_sha256"] == graph["inventory_sha256"]


def test_an_unknown_star_over_a_dbt_source_names_the_source(dbt_docs):
    _, columns, _ = dbt_docs

    (found,) = [u for u in columns["unresolved"] if u["reason"] == "unknown_star"]
    assert found.get("source")


DYNAMIC_MODEL = {
    "proj/dbt_project.yml": 'name: proj\nversion: "1.0"\nmodel-paths: ["models"]\n',
    "proj/models/stg.sql": "select r.id from {{ source('raw', 'pay') }} r\n",
    "proj/models/dyn.sql": "{{ config(alias=var('a')) }}\nselect s.id, 7 as seven from {{ ref('stg') }} s\n",
    "proj/models/sources.yml": "version: 2\nsources:\n  - name: raw\n    tables:\n      - name: pay\n",
}


def test_every_edge_into_a_dynamic_model_from_a_column_is_inferred(make_repo, run_script, tmp_path):
    out = tmp_path / "out"
    _scan_and_import(run_script, make_repo(DYNAMIC_MODEL), out)
    columns = json.loads((out / "columns.json").read_text(encoding="utf-8"))

    edge = _edge(columns, "col:{}.stg.id", "col:dyn.id")
    assert (edge["status"], edge["rule"]) == ("inferred", "dynamic_model")


def test_a_constant_edge_into_a_dynamic_model_is_inferred(make_repo, run_script, tmp_path):
    out = tmp_path / "out"
    _scan_and_import(run_script, make_repo(DYNAMIC_MODEL), out)
    columns = json.loads((out / "columns.json").read_text(encoding="utf-8"))

    (edge,) = [e for e in columns["edges"] if e["to"] == "col:dyn.seven"]
    assert edge["status"] == "inferred"


# -- the CLI ----------------------------------------------------------------------------
def test_imports_writes_identical_columns_json_twice(make_repo, run_script, tmp_path):
    repo = make_repo({"q/a.sql": "CREATE TABLE s.a AS SELECT r.id FROM raw.x r;\n", "q/b.sql": "SELECT * FROM s.a;\n"})
    first, second = tmp_path / "one", tmp_path / "two"
    _scan_and_import(run_script, repo, first)
    _scan_and_import(run_script, repo, second)

    assert (first / "columns.json").read_bytes() == (second / "columns.json").read_bytes()


def test_imports_removes_a_stale_columns_json_when_no_sql_is_left(make_repo, run_script, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "columns.json").write_text("{}", encoding="utf-8")

    summary = _scan_and_import(run_script, make_repo({"a.py": "import os\n"}), out)

    assert not (out / "columns.json").exists()
    assert "columns" not in summary


def test_lineage_resolve_keeps_its_output_without_matched():
    writers = lineage.index([("a.sql", "lake.s.t"), ("b.sql", "x.y")])

    results = lineage.resolve("s.t", False, writers) + lineage.resolve("x.y", False, writers)

    assert results == [{"status": "inferred", "to": "a.sql", "rule": "table_name"},
                       {"status": "verified", "to": "b.sql", "rule": "name"}]
    assert lineage.resolve_matched("s.t", False, writers)[0]["matched"] == "lake.s.t"


# -- fix round 1 ------------------------------------------------------------------------
def test_star_passthrough_edges_count_against_the_total_edge_limit(monkeypatch):
    monkeypatch.setattr("arche_lib.column_document.MAX_EDGES", 5)
    sources = {f"q/l{i:02}.sql": f"INSERT INTO s.t SELECT * FROM raw.r{i};\n" for i in range(20)}
    sources["q/z.sql"] = "SELECT t.a, t.b, t.c FROM s.t t;\n"

    columns = _columns(sources)

    assert len(columns["edges"]) <= 5
    assert columns["truncated"] is True


def test_star_passthrough_edges_count_against_their_files_edge_limit(monkeypatch):
    monkeypatch.setattr("arche_lib.column_document.MAX_FILE_EDGES", 2)
    columns = _columns({"q/a.sql": "CREATE TABLE s.t AS SELECT * FROM raw.r;\n",
                        "q/b.sql": "SELECT t.a, t.b FROM s.t t;\n", "q/c.sql": "SELECT t.c, t.d FROM s.t t;\n"})

    assert len([e for e in columns["edges"] if e["evidence"]["path"] == "q/a.sql"]) == 2
    assert columns["truncated"] is True


def test_constant_edges_count_against_the_file_edge_limit(monkeypatch):
    monkeypatch.setattr("arche_lib.column_document.MAX_FILE_EDGES", 2)
    columns = _columns({"q/a.sql": "CREATE TABLE s.t AS SELECT 1 AS a,\n2 AS b,\n3 AS c;\n"})

    assert len(columns["edges"]) == 2
    assert columns["truncated"] is True


def test_a_duplicate_edge_at_the_limit_does_not_truncate(monkeypatch):
    monkeypatch.setattr("arche_lib.column_document.MAX_FILE_EDGES", 1)
    statement = "CREATE TABLE s.t AS SELECT r.a AS c FROM raw.r r;"
    columns = _columns({"q/a.sql": f"{statement} {statement}\n"})  # the same edge twice on one line

    assert len(columns["edges"]) == 1
    assert columns["truncated"] is False


TEMP_PROBE = ("CREATE TEMP TABLE t AS SELECT x.a, nope FROM raw.x x JOIN raw.y y ON x.k = y.k "
              "WHERE x.status = 'p';\nINSERT INTO s.m (a, b) SELECT a, nope FROM t;\n")


def test_a_temporary_tables_join_keys_and_filters_reach_the_table_that_reads_it():
    columns = _columns({"q/t.sql": TEMP_PROBE})

    uses = {(e["from"], e["to"]) for e in columns["edges"] if e["kind"] in ("join_key", "filter")}
    assert {("col:raw.x.k", "table:s.m"), ("col:raw.y.k", "table:s.m"), ("col:raw.x.status", "table:s.m")} <= uses
    assert ("nope", "col:s.m.b") in [(u["spec"], u["to"]) for u in columns["unresolved"]]
    assert not any(c["table"] in ("t", "temp:t") for c in columns["columns"])


def test_a_temporary_table_nobody_reads_keeps_its_uses_on_its_own_name():
    columns = _columns({"q/t.sql": "CREATE TEMP TABLE t AS SELECT r.a FROM raw.x r WHERE r.f = 1;\n"})

    assert ("col:raw.x.f", "table:temp:t") in [(e["from"], e["to"]) for e in columns["edges"]]


def test_a_temporary_table_with_a_tail_or_a_statement_miss_is_partial():
    columns = _columns({"q/t.sql": "CREATE TEMP TABLE t AS SELECT r.a FROM raw.x r extra junk;\n"
                                   "CREATE TEMP TABLE u (a, b) AS SELECT r.a FROM raw.x r;\n"
                                   "INSERT INTO s.m (a) SELECT a FROM u;\n"})

    assert [(s["status"], s["reason"]) for s in columns["statements"]][:2] == [("partial", "tail"),
                                                                                ("partial", "arity")]
    assert ("table:s.m", "arity") in [(u["to"], u["reason"]) for u in columns["unresolved"]]


def test_an_insert_column_list_does_not_outrank_ddl():
    columns = _columns({"q/a.sql": "CREATE TABLE s.t (a INT, b INT, c INT);\n",
                        "q/b.sql": "INSERT INTO s.t (a) SELECT r.a FROM raw.r r;\n",
                        "q/c.sql": "CREATE TABLE s.u AS SELECT * FROM s.t;\n"})

    assert sorted(e["to"] for e in columns["edges"] if e["to"].startswith("col:s.u.")) == [
        "col:s.u.a", "col:s.u.b", "col:s.u.c"]
    assert columns["column_sources_disagree"] == []


def test_insert_column_lists_merge_into_one_open_list():
    columns = _columns({"q/a.sql": "INSERT INTO s.t (a) SELECT r.a FROM raw.r r;\n",
                        "q/b.sql": "INSERT INTO s.t (b) SELECT r.b FROM raw.r r;\n",
                        "q/c.sql": "CREATE TABLE s.u AS SELECT * FROM s.t;\n"})

    into_u = [e for e in columns["edges"] if e["to"].startswith("col:s.u.")]
    assert sorted(e["to"] for e in into_u) == ["col:s.u.a", "col:s.u.b"]
    assert all(e["status"] == "inferred" for e in into_u)
    assert ("table:s.u", "unknown_star") in [(u["to"], u["reason"]) for u in columns["unresolved"]]
    assert [c["known_from"] for c in columns["columns"] if c["id"] == "col:s.t.a"] == [["sql_output"]]


def test_a_read_over_an_insert_column_list_is_not_verified():
    columns = _columns({"q/a.sql": "INSERT INTO s.t (a) SELECT r.a FROM raw.r r;\n",
                        "q/b.sql": "SELECT a FROM s.t;\n"})

    (edge,) = [e for e in columns["edges"] if e["from"] == "col:s.t.a"]
    assert edge["status"] != "verified"


def test_star_passthrough_reaches_a_column_ddl_lists():
    columns = _columns({"q/a.sql": "CREATE TABLE s.t (a INT, b INT);\n",
                        "q/b.sql": "INSERT INTO s.t SELECT * FROM raw.r;\n",
                        "q/c.sql": "SELECT t.a FROM s.t t;\n"})

    assert _edges(columns, rule="star_passthrough") == [
        ("col:raw.r.a", "col:s.t.a", "direct", "inferred", "star_passthrough")]


DBT_PROJECT = {"proj/dbt_project.yml": 'name: proj\nversion: "1.0"\nmodel-paths: ["models"]\n'}


def test_a_dbt_model_whose_first_query_fails_to_parse_has_no_later_query(make_repo, run_script, tmp_path):
    out = tmp_path / "out"
    repo = make_repo({**DBT_PROJECT, "proj/models/m.sql": "select from;\nselect r.id from raw.x r\n"})
    _scan_and_import(run_script, repo, out)
    columns = json.loads((out / "columns.json").read_text(encoding="utf-8"))

    assert not any(c["table"] == "{}.m" for c in columns["columns"])
