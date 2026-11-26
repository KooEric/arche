"""dbt property files and dbt_project.yml folder settings, as yaml_config extracts them."""
from arche_lib.langs import yaml_config

PROPS = """version: 2
models:
  - name: orders
    description: >
      One row per order.
      Placed or shipped.
    data_tests:
      - dbt_utils.expression_is_true:
          arguments:
            expression: "amount >= 0"
    columns:
      - name: order_id
        data_type: bigint
        description: "{{ doc('order_id') }}"
        tests:
          - not_null
          - unique
      - name: status
        tests:
          - accepted_values:
              values: ['placed', 'shipped', 'returned']
      - name: customer_id
        data_tests:
          - relationships:
              arguments:
                to: ref('customers')
                field: id
          - relationships:
              to: source('raw', 'customers')
              field: id
          - relationships:
              to: "{{ var('dim') }}"
              field: id
  - name: customers
    description: |
      Customers.

      # Kept as text.
snapshots:
  - name: orders_snapshot
"""

SOURCES = """version: 2
sources:
  - name: raw
    schema: raw_data
    database: lake
    description: Landing tables
    tables:
      - name: events
        identifier: ev_v2
        description: Click events
        columns:
          - name: event_id
            tests: [not_null]
      - name: customers
"""


def _props(text, path="dbt/models/schema.yml"):
    return yaml_config.extract(path, text)["dbt_props"]


def test_models_carry_descriptions_columns_types_and_tests():
    models = _props(PROPS)["models"]
    orders = models[0]

    assert [(m["kind"], m["name"], m["line"]) for m in models] == [
        ("models", "orders", 3), ("models", "customers", 34), ("snapshots", "orders_snapshot", 40)]
    assert orders["description"] == "One row per order. Placed or shipped.\n"
    assert orders["tests"] == [{"name": "dbt_utils.expression_is_true"}]
    assert orders["columns"][0] == {"name": "order_id", "description": None, "doc": "order_id", "data_type": "bigint",
                                    "tests": [{"name": "not_null"}, {"name": "unique"}]}
    assert models[1]["description"] == "Customers.\n\n# Kept as text.\n"


def test_accepted_values_and_relationships_keep_their_main_arguments():
    columns = _props(PROPS)["models"][0]["columns"]

    assert columns[1]["tests"] == [{"name": "accepted_values", "values": ["placed", "shipped", "returned"], "values_count": 3}]
    assert columns[2]["tests"] == [
        {"name": "relationships", "to": {"ref": "customers"}, "field": "id"},
        {"name": "relationships", "to": {"source": ["raw", "customers"]}, "field": "id"},
        {"name": "relationships", "to": {"dynamic": True}, "field": "id"},
    ]


def test_a_doc_only_description_names_the_doc_block():
    text = "models:\n  - name: a\n    description: '{{ doc(\"a_doc\") }}'\n"

    assert _props(text)["models"][0]["doc"] == "a_doc"
    assert _props(text)["models"][0]["description"] is None


def test_sources_carry_schema_database_identifier_and_columns():
    source = _props(SOURCES, "dbt/models/staging/sources.yml")["sources"][0]

    assert (source["name"], source["schema"], source["database"], source["line"]) == ("raw", "raw_data", "lake", 3)
    assert [(t["name"], t["identifier"], t["line"]) for t in source["tables"]] == [("events", "ev_v2", 8), ("customers", None, 14)]
    assert source["tables"][0]["columns"] == [{"name": "event_id", "description": None, "data_type": None,
                                              "tests": [{"name": "not_null"}]}]


def test_accepted_values_keep_ten_values_and_the_count():
    values = ", ".join(f"'v{i}'" for i in range(14))
    text = f"models:\n  - name: a\n    columns:\n      - name: c\n        tests:\n          - accepted_values:\n              values: [{values}]\n"

    test = _props(text)["models"][0]["columns"][0]["tests"][0]
    assert (test["values"], test["values_count"]) == ([f"v{i}" for i in range(10)], 14)


def test_long_descriptions_are_cut_and_marked():
    body = "".join(f"      words {i:04d} and more words\n" for i in range(200))
    model = _props(f"models:\n  - name: a\n    description: |\n{body}  - name: b\n    description: '{'x' * 2500}'\n")["models"]

    assert len(model[0]["description"]) == 2000 and model[0]["description_truncated"] is True
    assert len(model[1]["description"]) == 2000 and model[1]["description_truncated"] is True


def test_odd_shapes_and_deep_nesting_do_not_break_extraction():
    deep = "".join(" " * (14 + 2 * depth) + f"k{depth}:\n" for depth in range(1500)) + " " * 3014 + "end: x\n"
    text = ("models:\n  - name: a\n    columns:\n      - name: c\n        tests:\n"
            "          - accepted_values:\n              values: placed\n          - deep:\n" + deep)

    tests = _props(text)["models"][0]["columns"][0]["tests"]
    assert tests == [{"name": "accepted_values", "values": [], "values_count": 0}, {"name": "deep"}]


def test_yaml_without_dbt_keys_gets_no_props():
    assert "dbt_props" not in yaml_config.extract("compose.yaml", "services:\n  web:\n    build: ./web\n")


def test_folder_settings_come_from_the_project_block_with_or_without_plus():
    text = ("name: jaffle\n"
            "models:\n"
            "  jaffle:\n"
            "    +schema: base\n"
            "    marts:\n"
            "      +schema: marts\n"
            "      finance:\n"
            "        alias: fin\n"
            "  other_pkg:\n"
            "    +schema: ignored\n"
            "snapshots:\n"
            "  jaffle:\n"
            "    +schema: snaps\n")

    assert yaml_config.extract("dbt/dbt_project.yml", text)["dbt_folder_config"] == [
        {"kind": "models", "folders": [], "setting": "schema", "value": "base", "line": 4},
        {"kind": "models", "folders": ["marts"], "setting": "schema", "value": "marts", "line": 6},
        {"kind": "models", "folders": ["marts", "finance"], "setting": "alias", "value": "fin", "line": 8},
        {"kind": "snapshots", "folders": [], "setting": "schema", "value": "snaps", "line": 13},
    ]


def test_ref_and_source_names_in_relationships_are_capped():
    long_name = "y" * 5000
    text = f"models:\n  - name: a\n    columns:\n      - name: c\n        tests:\n          - relationships:\n              to: ref('{long_name}')\n              field: id\n"

    to = _props(text)["models"][0]["columns"][0]["tests"][0]["to"]
    assert to == {"ref": "y" * 200}


def test_source_description_truncation_is_kept():
    text = f"sources:\n  - name: raw\n    description: '{'x' * 3000}'\n    tables:\n      - name: t\n"

    source = _props(text)["sources"][0]
    assert len(source["description"]) == 2000 and source["description_truncated"] is True


def test_config_keys_under_a_folder_are_not_folder_settings():
    text = ("name: jaffle\n"
            "models:\n"
            "  jaffle:\n"
            "    +column_types:\n"
            "      schema: text\n"
            "    +meta:\n"
            "      alias: x\n"
            "    marts:\n"
            "      +schema: m\n")

    assert yaml_config.extract("dbt/dbt_project.yml", text)["dbt_folder_config"] == [
        {"kind": "models", "folders": ["marts"], "setting": "schema", "value": "m", "line": 9},
    ]


def test_long_model_name_and_folder_value_are_cut():
    long_name = "m" * 5000
    model = _props(f"models:\n  - name: {long_name}\n")["models"][0]
    assert len(model["name"]) == 200

    folder = yaml_config.extract("dbt/dbt_project.yml", f"name: p\nmodels:\n  p:\n    +schema: {'s' * 5000}\n")["dbt_folder_config"]
    assert len(folder[0]["value"]) == 200


def test_a_test_in_the_name_and_test_name_syntax_takes_the_test_name():
    text = ("models:\n  - name: a\n    columns:\n      - name: id\n        tests:\n"
            "          - name: my_custom\n            test_name: unique\n"
            "          - name: pick\n            test_name: accepted_values\n            values: [x, y]\n")

    tests = _props(text)["models"][0]["columns"][0]["tests"]

    assert tests == [{"name": "unique"}, {"name": "accepted_values", "values": ["x", "y"], "values_count": 2}]


def test_a_column_description_that_is_only_a_doc_call_records_the_doc_block():
    text = ("models:\n  - name: a\n    columns:\n      - name: id\n        description: \"{{ doc('id_doc') }}\"\n"
            "      - name: plain\n        description: text\n")

    columns = _props(text)["models"][0]["columns"]

    assert (columns[0]["description"], columns[0]["doc"]) == (None, "id_doc")
    assert "doc" not in columns[1]


def test_the_project_name_is_cut_at_the_value_limit():
    project = yaml_config.extract("dbt_project.yml", "name: " + "n" * 500 + "\n")["dbt_project"]

    assert len(project["name"]) == 200
