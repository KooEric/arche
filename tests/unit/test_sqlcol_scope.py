"""sqlcol.scope: how a column reference finds the table column behind it."""
import pytest

from arche_lib.sqlcol.derive import derive
from arche_lib.sqlcol.scope import TableInfo
from arche_lib.sqlcol.statements import parse
from arche_lib.sqlcol.tokens import tokens


def known(table, *names, listed_from="sql_output"):
    """A repository table whose columns SQL in the repository lists."""
    return TableInfo(table, "verified", "name", names, True, False, listed_from=listed_from)


def external(table):
    """A table no file writes: its columns are unknown."""
    return TableInfo(table, "external", None, None, False, False)


TABLES = {
    "orders": known("db.orders", "id", "amt", "status", "cust"),
    "db.orders": known("db.orders", "id", "amt", "status", "cust"),
    "cust": known("db.cust", "cust", "name", "region", listed_from="ddl"),
    "people": known("db.people", "pid", "region", listed_from="ddl"),
    "raw": external("raw.ev"),
    "raw2": external("raw.ev2"),
}


def derived(text, tables=None, temps=None):
    """The Derived of the only statement in `text`."""
    tables = TABLES if tables is None else tables

    def lookup(table):
        return tables.get(table.name, external(table.name))

    (statement,) = parse(list(tokens(text)))
    return derive(text, statement, lookup, {} if temps is None else temps)


def leaves(text, index=0, tables=None):
    """(table, column, status, rule) behind output `index`."""
    output = derived(text, tables).outputs[index]
    return [(leaf.table, leaf.column, leaf.status, leaf.rule) for leaf in output.leaves]


def misses(text, index=0):
    return [(miss.reason, miss.candidates) for miss in derived(text).outputs[index].misses]


def usages(text, tables=None):
    return [(u.kind, u.leaf.table, u.leaf.column, u.leaf.status, u.leaf.rule) for u in derived(text, tables).usages]


# -- names and qualifiers ---------------------------------------------------------------
def test_a_cte_named_like_a_real_table_hides_the_table():
    found = derived("WITH orders AS (SELECT 1 AS zz) SELECT zz FROM orders").outputs[0]

    assert found.leaves == () and found.misses == ()


def test_an_inner_cte_hides_an_outer_cte_of_the_same_name():
    text = "WITH c AS (SELECT amt AS x FROM orders) SELECT x FROM (WITH c AS (SELECT id AS x FROM orders) " \
           "SELECT x FROM c) d"

    assert leaves(text) == [("db.orders", "id", "verified", "listed")]


def test_a_qualified_reference_by_alias_is_verified_qualified():
    assert leaves("SELECT o.amt FROM orders o") == [("db.orders", "amt", "verified", "qualified")]


def test_a_qualified_reference_by_the_full_table_name():
    assert leaves("SELECT db.orders.amt FROM db.orders") == [("db.orders", "amt", "verified", "qualified")]


def test_a_qualified_reference_by_the_last_part_of_the_table_name():
    assert leaves("SELECT orders.amt FROM db.orders") == [("db.orders", "amt", "verified", "qualified")]


def test_a_qualified_reference_to_an_external_table_is_verified():
    found = derived("SELECT q.x FROM raw q").outputs[0].leaves[0]

    assert (found.table, found.column, found.status, found.rule, found.external) == ("raw.ev", "x", "verified",
                                                                                    "qualified", True)


def test_a_correlated_subquery_reaches_the_outer_source():
    text = "SELECT o.amt FROM orders o WHERE EXISTS (SELECT 1 FROM cust c WHERE c.cust = o.cust)"

    assert sorted(usages(text)) == [("filter", "db.cust", "cust", "verified", "qualified"),
                                    ("filter", "db.orders", "cust", "verified", "qualified")]


def test_an_unqualified_correlated_reference_falls_back_to_the_outer_scope():
    text = "SELECT amt FROM orders o WHERE EXISTS (SELECT 1 FROM cust WHERE name = status)"

    assert ("filter", "db.orders", "status", "verified", "listed") in usages(text)


def test_a_struct_field_hangs_on_its_column():
    assert leaves("SELECT amt.sub FROM orders") == [("db.orders", "amt", "inferred", "struct_field")]


def test_a_struct_field_behind_a_table_alias_hangs_on_its_column():
    assert leaves("SELECT o.amt.sub FROM orders o") == [("db.orders", "amt", "inferred", "struct_field")]


def test_an_unknown_qualifier_is_reported():
    assert misses("SELECT nope.x FROM orders o") == [("unknown_qualifier", ())]


# -- unqualified references: the spec table, row by row --------------------------------
def test_listed_in_the_only_source_is_verified_listed():
    assert leaves("SELECT amt FROM orders") == [("db.orders", "amt", "verified", "listed")]


def test_listed_in_one_source_with_an_open_source_beside_it_is_known_source():
    assert leaves("SELECT amt FROM orders, raw") == [("db.orders", "amt", "inferred", "known_source")]


def test_listed_in_two_sources_is_ambiguous_with_both_candidates():
    assert misses("SELECT cust FROM orders, cust") == [("ambiguous", ("cust", "orders"))]


def test_listed_nowhere_with_a_single_open_source_is_single_source():
    assert leaves("SELECT x FROM raw") == [("raw.ev", "x", "inferred", "single_source")]


def test_listed_nowhere_with_one_open_source_among_others_is_only_open_source():
    assert leaves("SELECT x FROM orders, raw") == [("raw.ev", "x", "inferred", "only_open_source")]


def test_listed_nowhere_with_two_open_sources_is_ambiguous():
    assert misses("SELECT x FROM raw, raw2") == [("ambiguous", ("raw", "raw2"))]


def test_in_no_source_at_all_is_unknown_column():
    assert misses("SELECT zzz FROM orders") == [("unknown_column", ())]


def test_a_using_column_hangs_on_every_source_that_may_have_it():
    assert leaves("SELECT cust FROM orders JOIN cust USING (cust)") == [
        ("db.orders", "cust", "inferred", "using"), ("db.cust", "cust", "inferred", "using")]


def test_a_using_column_is_a_join_key_of_both_tables():
    assert sorted(u[:3] for u in usages("SELECT amt FROM orders JOIN cust USING (cust)")) == [
        ("join_key", "db.cust", "cust"), ("join_key", "db.orders", "cust")]


# -- SELECT aliases in other clauses -----------------------------------------------------
def test_a_select_alias_in_group_by_uses_the_columns_behind_it():
    assert usages("SELECT amt AS a FROM orders GROUP BY a") == [
        ("group_key", "db.orders", "amt", "inferred", "select_alias_ref")]


def test_a_select_alias_in_where_uses_the_columns_behind_it():
    assert usages("SELECT amt + 1 AS a FROM orders WHERE a > 0") == [
        ("filter", "db.orders", "amt", "inferred", "select_alias_ref")]


def test_a_source_column_wins_over_a_select_alias_of_the_same_name():
    assert usages("SELECT id AS amt FROM orders WHERE amt > 0") == [
        ("filter", "db.orders", "amt", "verified", "listed")]


def test_group_by_one_uses_the_first_output_column():
    assert usages("SELECT status, SUM(amt) FROM orders GROUP BY 1") == [
        ("group_key", "db.orders", "status", "verified", "listed")]


def test_group_by_a_number_past_the_outputs_uses_nothing():
    assert usages("SELECT status FROM orders GROUP BY 3") == []


def test_a_qualify_alias_reference_uses_the_columns_behind_it():
    text = "SELECT ROW_NUMBER() OVER (ORDER BY id) AS rn FROM orders QUALIFY rn = 1"

    assert ("order_key", "db.orders", "id", "verified", "listed") in usages(text)


# -- which source a qualifier names (fix round 1) ----------------------------------------
QUALIFIED = {**TABLES, "x.t": known("db.xt", "c"), "u": known("db.u", "c"), "v": known("db.v", "c")}


def test_a_qualifier_matching_the_alias_wins_over_a_table_of_that_name():
    found = derived("SELECT t.c FROM x.t JOIN u AS t ON 1 = 1", QUALIFIED).outputs[0].leaves

    assert [(l.table, l.status) for l in found] == [("db.u", "verified")]


def test_a_table_with_an_alias_is_not_named_by_its_table_name():
    found = derived("SELECT u.c FROM u AS a JOIN x.t AS u ON 1 = 1", QUALIFIED).outputs[0].leaves

    assert [l.table for l in found] == ["db.xt"]


def test_an_aliased_table_named_by_its_table_name_is_an_unknown_qualifier():
    found = derived("SELECT u.c FROM u AS a", QUALIFIED).outputs[0]

    assert (found.leaves, [m.reason for m in found.misses]) == ((), ["unknown_qualifier"])


def test_two_tables_of_one_name_without_aliases_are_ambiguous():
    tables = {**QUALIFIED, "y.t": known("db.yt", "c")}

    found = derived("SELECT t.c FROM x.t, y.t", tables).outputs[0]

    assert found.leaves == ()
    assert [(m.reason, m.candidates) for m in found.misses] == [("ambiguous", ("x.t", "y.t"))]
