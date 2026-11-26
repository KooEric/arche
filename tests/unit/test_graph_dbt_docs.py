"""graph.json for dbt: model tables, source names and property docs."""
from test_dbt_and_path_values import _graph

REPO = {
    "dbt/dbt_project.yml": ("name: jaffle\n"
                            "models:\n"
                            "  jaffle:\n"
                            "    marts:\n"
                            "      +schema: marts\n"),
    "dbt/models/staging/sources.yml": ("version: 2\n"
                                       "sources:\n"
                                       "  - name: raw\n"
                                       "    schema: raw_data\n"
                                       "    tables:\n"
                                       "      - name: events\n"
                                       "        identifier: ev_v2\n"
                                       "        description: Click events\n"
                                       "      - name: orders\n"),
    "dbt/models/staging/stg_events.sql": "select * from {{ source('raw', 'events') }}\n",
    "dbt/models/staging/stg_orders.sql": "select * from {{ source('raw', 'orders') }}\n",
    "dbt/models/marts/orders.sql": ("{{ config(materialized='table') }}\n"
                                    "select * from {{ ref('stg_orders') }} join {{ ref('stg_events') }} on true\n"
                                    "join {}.stg_orders x on true\n"),
    "dbt/models/marts/schema.yml": ("version: 2\n"
                                    "models:\n"
                                    "  - name: orders\n"
                                    "    description: >\n"
                                    "      One row per order.\n"
                                    "    columns:\n"
                                    "      - name: order_id\n"
                                    "        data_type: bigint\n"
                                    "        tests: [not_null, unique]\n"
                                    "  - name: orders\n"
                                    "  - name: old_model\n"),
    "reports/weekly.sql": "select * from analytics_marts.orders join lake.raw_data.ev_v2 e on true\n",
}


def test_model_tables_join_written_tables_with_via():
    written = [item for item in _graph(REPO)["written_tables"]]

    assert written == [
        {"table": "{}.stg_events", "from": "file:dbt/models/staging/stg_events.sql", "line": 1, "via": "dbt:model"},
        {"table": "{}.stg_orders", "from": "file:dbt/models/staging/stg_orders.sql", "line": 1, "via": "dbt:model"},
        {"table": "{}_marts.orders", "from": "file:dbt/models/marts/orders.sql", "line": 1, "via": "dbt:model"},
    ]


def test_sql_outside_dbt_reads_a_model_table_by_inference():
    edges = [(e["from"], e["to"], e["table"], e["status"], e.get("rule")) for e in _graph(REPO)["edges"]
             if e["kind"] == "READS" and e["from"] == "file:reports/weekly.sql"]

    assert edges == [("file:reports/weekly.sql", "file:dbt/models/marts/orders.sql", "analytics_marts.orders",
                      "inferred", "table_name")]


def test_source_reads_take_their_names_from_sources_yml_and_never_match_model_tables():
    graph = _graph(REPO)

    assert graph["external_tables"] == [
        {"from": "file:dbt/models/staging/stg_events.sql", "table": "raw_data.ev_v2", "line": 1},
        {"from": "file:dbt/models/staging/stg_orders.sql", "table": "raw_data.orders", "line": 1},
        {"from": "file:reports/weekly.sql", "table": "lake.raw_data.ev_v2", "line": 1},
    ]


def test_a_pair_linked_by_ref_gets_no_second_edge_by_table_name():
    edges = [(e["to"], e.get("via"), e["table"]) for e in _graph(REPO)["edges"]
             if e["kind"] == "READS" and e["from"] == "file:dbt/models/marts/orders.sql"]

    assert edges == [("file:dbt/models/staging/stg_events.sql", "dbt:ref", "stg_events"),
                     ("file:dbt/models/staging/stg_orders.sql", "dbt:ref", "stg_orders")]


def test_dbt_models_carry_the_table_docs_and_duplicates():
    models = {item["model"]: item for item in _graph(REPO)["dbt_models"]}

    assert [item["file"] for item in _graph(REPO)["dbt_models"]] == [
        "file:dbt/models/marts/orders.sql", "file:dbt/models/staging/stg_events.sql", "file:dbt/models/staging/stg_orders.sql"]
    assert models["orders"] == {
        "model": "orders", "kind": "models", "project": "jaffle", "file": "file:dbt/models/marts/orders.sql",
        "table": "{}_marts.orders", "dynamic": False, "alias_from": "default", "schema_from": "project",
        "description": "One row per order.\n", "doc": None, "tests": [],
        "columns": [{"name": "order_id", "description": None, "data_type": "bigint",
                     "tests": [{"name": "not_null"}, {"name": "unique"}]}],
        "defined_in": {"path": "dbt/models/marts/schema.yml", "line": 3},
        "duplicates": [{"path": "dbt/models/marts/schema.yml", "line": 10}],
    }
    assert (models["stg_events"]["description"], models["stg_events"]["columns"], models["stg_events"]["defined_in"]) == (None, [], None)


def test_property_entries_naming_no_model_file_are_unlinked():
    assert _graph(REPO)["dbt_docs_unlinked"] == [
        {"name": "old_model", "kind": "models", "defined_in": {"path": "dbt/models/marts/schema.yml", "line": 11}}]


def test_dbt_sources_list_the_definitions_source_calls_used():
    graph = _graph({**REPO, "dbt/models/staging/again.yml": "sources:\n  - name: raw\n    tables:\n      - name: orders\n"})

    assert graph["dbt_sources"] == [
        {"source": "raw", "table": "events", "resolved": "raw_data.ev_v2", "description": "Click events", "columns": [],
         "defined_in": {"path": "dbt/models/staging/sources.yml", "line": 6}, "status": "resolved"},
        {"source": "raw", "table": "orders", "resolved": "raw.orders", "description": None, "columns": [], "defined_in": None,
         "candidates": [{"path": "dbt/models/staging/again.yml", "line": 4}, {"path": "dbt/models/staging/sources.yml", "line": 9}],
         "status": "ambiguous"},
    ]


def test_without_a_dbt_project_nothing_changes():
    graph = _graph({key: value for key, value in REPO.items() if key != "dbt/dbt_project.yml"})

    assert (graph["dbt_models"], graph["dbt_sources"], graph["dbt_docs_unlinked"]) == ([], [], [])
    assert graph["written_tables"] == []
    assert {item["table"] for item in graph["external_tables"]} == {
        "raw.events", "raw.orders", "{}.stg_orders", "analytics_marts.orders", "lake.raw_data.ev_v2"}


def test_the_same_input_gives_the_same_graph():
    import json

    assert json.dumps(_graph(REPO), sort_keys=True) == json.dumps(_graph(dict(reversed(list(REPO.items())))), sort_keys=True)
