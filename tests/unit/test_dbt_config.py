"""`{{ config(alias=..., schema=...) }}` in dbt model SQL."""
from arche_lib.langs import sql
from arche_lib.langs.dbt_config import config


def test_literal_alias_and_schema_in_either_quote_style():
    found = config("{{ config(materialized='table', alias='Orders_Final', schema=\"marts\") }}\nselect 1\n")

    assert found == {"line": 1, "alias": "Orders_Final", "schema": "marts", "dynamic": [], "branched": False}


def test_a_value_that_is_not_a_literal_is_dynamic():
    found = config("select 1\n{{ config(alias=var('name'), schema='s' ~ target.name, tags=['a', 'b']) }}\n")

    assert found == {"line": 2, "alias": None, "schema": None, "dynamic": ["alias", "schema"], "branched": False}


def test_a_call_inside_if_or_for_is_branched_and_jinja_comments_hide_calls():
    branched = config("{% if target.name == 'prod' %}\n{{ config(alias='a') }}\n{% endif %}\n")
    after = config("{% if x %}{% endif %}\n{{ config(alias='a') }}\n")
    hidden = config("{# {{ config(alias='a') }} #}\nselect 1\n")

    assert (branched["alias"], branched["branched"]) == ("a", True)
    assert after["branched"] is False
    assert hidden is None


def test_later_calls_override_earlier_ones_key_by_key():
    found = config("{{ config(alias='first', schema='s') }}\n-- {{ config(alias='second') }}\nselect 1\n")

    assert (found["alias"], found["schema"], found["line"]) == ("second", "s", 2)


def test_nested_arguments_and_an_unclosed_call_are_handled():
    nested = config("{{ config(partition_by={'field': 'd', 'range': (1, 2)}, alias='x') }}\n")
    unclosed = config("{{ config(alias='x'\nselect 1\n")

    assert nested["alias"] == "x"
    assert unclosed is None


def test_sql_extract_adds_dbt_config_only_when_a_call_is_found():
    assert sql.extract("m.sql", "{{ config(alias='x') }}\nselect 1\n")["dbt_config"]["alias"] == "x"
    assert "dbt_config" not in sql.extract("q.sql", "select * from a.config\n")


def test_many_unclosed_calls_take_bounded_work():
    text = "{{ config(" * 20_000

    assert config(text) is None


def test_many_calls_and_branches_keep_line_numbers_and_finish_quickly():
    text = "{% if a %}{% endif %}\n{{ config(alias='x') }}\n" * 20_000

    found = config(text)
    assert (found["line"], found["branched"]) == (40_000, False)
