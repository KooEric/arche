import hashlib

from arche_lib.changes import _dependents, _reverse_imports
from arche_lib.graph import build_graph
from arche_lib.inventory import count_lines
from arche_lib.langs import language_for, yaml_config
from arche_lib.langs.sql_tables import tables


def test_ref_calls_become_ref_records_and_are_not_table_reads():
    found = tables("models/x.sql", (
        "-- {{ ref('commented') }}\n"
        "with o as (select * from {{ ref('stg_orders') }}),\n"
        "c as (select * from {{ ref(\"pkg\", 'stg_customers', v=2) }} as x)\n"
        "select * from o join c on o.id = c.id\n"))

    assert found["tables"] == []
    # Jinja renders before SQL, so a ref in a `--` comment is still a dependency.
    assert [(r["ref"], r["package"], r["line"], r["dynamic"]) for r in found["dbt_refs"]] == [
        ("commented", None, 1, False), ("stg_orders", None, 2, False), ("stg_customers", "pkg", 3, False)]


def test_every_jinja_ref_is_a_dependency_but_jinja_comments_are_not():
    found = tables("models/x.sql", (
        "{{ ref('bare') }}\n"
        "-- from {{ ref('in_sql_comment') }}\n"
        "{# {{ ref('in_jinja_comment') }} #}\n"
        "{% set rel = ref('in_statement') %}\n"
        "select * from {{ rel }}\n"))

    assert [(r["ref"], r["line"]) for r in found["dbt_refs"]] == [("bare", 1), ("in_sql_comment", 2), ("in_statement", 4)]


def test_source_call_is_a_read_of_the_source_table():
    found = tables("models/x.sql", "select * from {{ source('raw', 'Payments') }} p\n")

    assert [(t["table"], t["access"], t["dynamic"]) for t in found["tables"]] == [("raw.payments", "read", False)]
    assert found["dbt_refs"] == []


def test_ref_with_a_non_literal_argument_is_dynamic():
    found = tables("models/x.sql", "select * from {{ ref(model_name) }}\n")

    assert [(r["ref"], r["dynamic"]) for r in found["dbt_refs"]] == [("", True)]


def test_dbt_project_and_packages_and_property_names_are_extracted():
    project = yaml_config.extract("dbt/dbt_project.yml", "name: shop\nmodel-paths: [\"models\", \"marts\"]\nseed-paths: [data]\n")
    packages = yaml_config.extract("dbt/packages.yml", "packages:\n  - local: ../shared\n  - package: dbt-labs/dbt_utils\n")
    props = yaml_config.extract("dbt/models/schema.yml", "version: 2\nmodels:\n  - name: orders\n    columns:\n      - name: id\n")

    assert project["dbt_project"] == {"name": "shop", "model-paths": ["models", "marts"],
                                      "snapshot-paths": ["snapshots"], "seed-paths": ["data"]}
    assert packages["dbt_local_packages"] == ["shared"]
    assert [ref["via"] for ref in packages["path_refs"]] == ["dbt:packages.local"]
    assert packages["config_externals"] == [{"package": "dbt-labs/dbt_utils", "line": 3}]
    assert [(n["name"], n["kind"], n["line"]) for n in props["dbt_names"]] == [("orders", "models", 3)]


DBT = {
    "dbt/dbt_project.yml": "name: shop\nmodel-paths: [models]\nseed-paths: [seeds]\n",
    "dbt/packages.yml": "packages:\n  - local: ../shared\n",
    "dbt/models/staging/stg_orders.sql": "select * from {{ source('raw', 'orders') }}\n",
    "dbt/models/marts/orders.sql": (
        "select * from {{ ref('stg_orders') }}\n"
        "join {{ ref('countries') }} on true\n"
        "join {{ ref('shared_dim') }} on true\n"
        "join {{ ref('missing') }} on true\n"
        "join {{ ref('dbt_utils', 'date_spine') }} on true\n"),
    "dbt/models/marts/schema.yml": "version: 2\nmodels:\n  - name: orders\n  - name: ghost\n",
    "dbt/seeds/countries.csv": "code\nKR\n",
    "shared/dbt_project.yml": "name: shared\n",
    "shared/models/shared_dim.sql": "select 1 as id\n",
}


def _graph(sources):
    files = []
    for path, text in sources.items():
        measured = {"bytes": len(text), "lines": count_lines(text), "sha256": hashlib.sha256(text.encode()).hexdigest()}
        if language_for(path):
            files.append({"path": path, "status": "analyzed", "lang": language_for(path), **measured})
        else:
            files.append({"path": path, "status": "skipped", "reason": "unsupported_lang", **measured})
    inventory = {"schema_version": "0.1", "repo": {"name": "demo", "root": "/abs/demo", "commit": None, "dirty": None},
                 "files": files}
    return build_graph(inventory, sources.get)


def test_refs_resolve_in_the_own_project_then_local_packages():
    graph = _graph(DBT)
    reads = {(e["table"], e["status"], e["to"] or e["reason"]) for e in graph["edges"]
             if e["kind"] == "READS" and e["from"] == "file:dbt/models/marts/orders.sql"}

    assert reads == {
        ("stg_orders", "verified", "file:dbt/models/staging/stg_orders.sql"),
        ("countries", "unresolved", "target_skipped"),
        ("shared_dim", "verified", "file:shared/models/shared_dim.sql"),
        ("missing", "unresolved", "not_found"),
    }
    assert {"from": "file:dbt/models/marts/orders.sql", "package": "dbt_utils", "line": 5} in graph["externals"]
    assert {"from": "file:dbt/models/staging/stg_orders.sql", "table": "raw.orders", "line": 1} in graph["external_tables"]


def test_ref_edges_carry_the_dbt_rule_and_via():
    edge = next(e for e in _graph(DBT)["edges"] if e["kind"] == "READS" and e.get("to") == "file:dbt/models/staging/stg_orders.sql")

    assert (edge["rule"], edge["via"]) == ("dbt_ref", "dbt:ref")


def test_property_file_names_link_to_model_files():
    refs = {(e["via"], e["status"], e["to"] or e["reason"]) for e in _graph(DBT)["edges"]
            if e["kind"] == "REFERENCES" and e["from"] == "file:dbt/models/marts/schema.yml"}

    assert refs == {("dbt:models.name", "verified", "file:dbt/models/marts/orders.sql"),
                    ("dbt:models.name", "unresolved", "not_found")}


def test_changing_a_staging_model_reaches_the_mart_that_refs_it():
    reverse = _reverse_imports(_graph(DBT))

    assert "dbt/models/marts/orders.sql" in _dependents(reverse, "dbt/models/staging/stg_orders.sql")


def test_ref_outside_any_dbt_project_is_unresolved():
    graph = _graph({"sql/a.sql": "select * from {{ ref('b') }}\n", "sql/b.sql": "select 1\n"})

    edge = next(e for e in graph["edges"] if e["kind"] == "READS")
    assert (edge["status"], edge["reason"]) == ("unresolved", "no_dbt_project")


def test_schema_free_yaml_links_only_values_that_name_existing_files():
    result = yaml_config.extract("dags/team_a/load.yaml", (
        "load:\n"
        "  tasks:\n"
        "    - callable_file: callables/extract.py\n"
        "      cfg: ../shared/conf.yaml\n"
        "      image: python:3.12\n"
        "      note: see main.py now\n"
        "      url: https://x/y.py\n"
        "      tmpl: '{{ ds }}/run.py'\n"))

    assert [(ref["via"], ref["spec"]) for ref in result["path_refs"]] == [
        ("path_value:load.tasks.[].callable_file", "callables/extract.py"),
        ("path_value:load.tasks.[].cfg", "../shared/conf.yaml"),
    ]


TEAM = {
    "dags/team_a/load.yaml": ("tasks:\n"
                              "  - callable_file: callables/extract.py\n"
                              "    helper: dags/common/util.py\n"
                              "    tail: common/util.py\n"
                              "    missing: callables/none.py\n"
                              "    sql: sql/load.sql\n"),
    "dags/team_a/callables/extract.py": "def run(): pass\n",
    "dags/common/util.py": "X = 1\n",
    "dags/team_a/sql/load.sql": "select 1\n",
}


def test_path_values_resolve_by_own_folder_root_and_unique_suffix_and_drop_misses():
    graph = _graph(TEAM)
    refs = sorted((e.get("via", ""), e["status"], e.get("rule"), e["to"]) for e in graph["edges"] if e["kind"] == "REFERENCES")

    assert refs == [
        ("", "verified", "relative", "file:dags/team_a/sql/load.sql"),
        ("path_value:tasks.[].callable_file", "inferred", "path_value", "file:dags/team_a/callables/extract.py"),
        ("path_value:tasks.[].helper", "inferred", "path_value", "file:dags/common/util.py"),
        ("path_value:tasks.[].tail", "inferred", "path_value", "file:dags/common/util.py"),
    ]


VERSIONED = {
    "dbt_project.yml": "name: shop\n",
    "models/orders_v1.sql": "select 1\n",
    "models/orders_v2.sql": "select 2\n",
    "models/latest.sql": "select * from {{ ref('orders') }}\n",
    "models/pinned.sql": "select * from {{ ref('orders', v=1) }}\n",
}


def test_versioned_models_resolve_to_the_asked_or_the_highest_version():
    reads = {(e["from"], e["status"], e["rule"], e["to"]) for e in _graph(VERSIONED)["edges"] if e["kind"] == "READS"}

    assert reads == {
        ("file:models/latest.sql", "inferred", "dbt_latest_version", "file:models/orders_v2.sql"),
        ("file:models/pinned.sql", "verified", "dbt_ref", "file:models/orders_v1.sql"),
    }
