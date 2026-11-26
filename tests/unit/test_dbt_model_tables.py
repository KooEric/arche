"""dbt.py: the table each model builds and the real name of each source."""
from arche_lib import dbt
from arche_lib.langs import language_for
from arche_lib.langs.registry import adapter_for

PROJECT = ("name: jaffle\n"
           "model-paths: [models]\n"
           "snapshot-paths: [snapshots]\n"
           "models:\n"
           "  jaffle:\n"
           "    marts:\n"
           "      +schema: marts\n"
           "      finance:\n"
           "        schema: finance\n"
           "        +alias: '{{ var(\"x\") }}'\n"
           "    staging:\n"
           "      +alias: stg_all\n")


def _projects(sources):
    extracted = {path: adapter_for(language_for(path)).extract(path, text) for path, text in sources.items()}
    return dbt.build(sources, extracted), extracted


def _tables(sources):
    projects, extracted = _projects(sources)
    return {item["file"]: (item["table"], item["dynamic"], item["alias_from"], item["schema_from"], item["line"])
            for item in dbt.models(projects, extracted)}


def test_alias_and_schema_follow_config_then_the_deepest_folder_then_the_file_name():
    found = _tables({
        "dbt_project.yml": PROJECT,
        "models/marts/orders.sql": "select 1\n",
        "models/marts/orders_v2.sql": "select 2\n",
        "models/marts/named.sql": "\n{{ config(alias='Final', schema='Gold') }}\nselect 1\n",
        "models/marts/finance/revenue.sql": "select 1\n",
        "models/staging/stg_orders.sql": "select 1\n",
        "models/top.sql": "select 1\n",
        "snapshots/orders_snapshot.sql": "{% snapshot orders_snapshot %}\nselect 1\n{% endsnapshot %}\n",
    })

    assert found == {
        "models/marts/finance/revenue.sql": (None, True, "project", "project", 1),
        "models/marts/named.sql": ("{}_gold.final", False, "config", "config", 2),
        "models/marts/orders.sql": ("{}_marts.orders", False, "default", "project", 1),
        "models/marts/orders_v2.sql": ("{}_marts.orders_v2", False, "default", "project", 1),
        "models/staging/stg_orders.sql": ("{}.stg_all", False, "project", "default", 1),
        "models/top.sql": ("{}.top", False, "default", "default", 1),
        "snapshots/orders_snapshot.sql": ("{}.orders_snapshot", False, "default", "default", 1),
    }


def test_a_dynamic_alias_or_a_branched_config_has_no_table_and_a_dynamic_schema_is_dropped():
    found = _tables({
        "dbt_project.yml": "name: shop\n",
        "models/a.sql": "{{ config(alias=var('a')) }}\nselect 1\n",
        "models/b.sql": "{% if is_incremental() %}\n{{ config(alias='b') }}\n{% endif %}\nselect 1\n",
        "models/c.sql": "{{ config(schema=env_var('S')) }}\nselect 1\n",
    })

    assert found == {
        "models/a.sql": (None, True, "config", "default", 1),
        "models/b.sql": (None, True, "config", "default", 2),
        "models/c.sql": ("{}.c", False, "default", "config", 1),
    }


def test_sql_outside_model_paths_and_projects_without_models_name_no_table():
    assert _tables({"dbt_project.yml": "name: shop\n", "analyses/a.sql": "select 1\n", "x/b.sql": "select 1\n"}) == {}
    assert _tables({"q/a.sql": "select 1\n"}) == {}


SOURCES = {
    "dbt/dbt_project.yml": "name: shop\n",
    "dbt/packages.yml": "packages:\n  - local: ../shared\n",
    "dbt/models/sources.yml": ("sources:\n"
                               "  - name: raw\n"
                               "    schema: raw_data\n"
                               "    database: Lake\n"
                               "    tables:\n"
                               "      - name: events\n"
                               "        identifier: ev_v2\n"
                               "      - name: plain\n"
                               "  - name: var_schema\n"
                               "    schema: \"{{ var('raw_schema') }}\"\n"
                               "    tables:\n"
                               "      - name: t\n"),
    "dbt/models/dupe.yml": "sources:\n  - name: twice\n    tables:\n      - name: t\n",
    "dbt/models/dupe2.yml": "sources:\n  - name: twice\n    tables:\n      - name: t\n",
    "dbt/models/m.sql": "select 1\n",
    "shared/dbt_project.yml": "name: shared\n",
    "shared/models/sources.yml": "sources:\n  - name: pkg\n    schema: pkg_schema\n    tables:\n      - name: t\n",
    "shared/models/own.yml": "sources:\n  - name: raw\n    tables:\n      - name: events\n",
}


def _source(names, importer="dbt/models/m.sql"):
    projects, _ = _projects(SOURCES)
    found = dbt.resolve_source(names, importer, projects)
    return None if found is None else (found["status"], found["table"], [d["path"] for d in found["definitions"]])


def test_sources_use_schema_identifier_and_database_with_names_as_defaults():
    assert _source(["raw", "events"]) == ("resolved", "lake.raw_data.ev_v2", ["dbt/models/sources.yml"])
    assert _source(["raw", "plain"]) == ("resolved", "lake.raw_data.plain", ["dbt/models/sources.yml"])
    assert _source(["var_schema", "t"]) == ("resolved", "{}.t", ["dbt/models/sources.yml"])


def test_the_own_project_comes_before_local_packages():
    assert _source(["pkg", "t"]) == ("resolved", "pkg_schema.t", ["shared/models/sources.yml"])
    assert _source(["raw", "events"], "shared/models/x.sql") == ("resolved", "raw.events", ["shared/models/own.yml"])


def test_two_definitions_at_one_step_are_ambiguous_and_unknown_sources_are_none():
    assert _source(["twice", "t"]) == ("ambiguous", "twice.t", ["dbt/models/dupe.yml", "dbt/models/dupe2.yml"])
    assert _source(["nope", "t"]) is None
    assert _source(["raw", "events"], "elsewhere/q.sql") is None
