"""sqlcol.derive: each output column traced to table columns, with its kind, role and path."""
import pytest

from arche_lib.sqlcol.derive import derive
from arche_lib.sqlcol.scope import QueryRelation, TableInfo
from arche_lib.sqlcol.statements import parse
from arche_lib.sqlcol.tokens import tokens
from test_sqlcol_scope import TABLES, derived, external, known


def _outputs(text, tables=None, temps=None):
    """{output name: [(table, column, kind, condition, status, rule, via)]}."""
    return {output.name: [(l.table, l.column, l.kind, l.condition, l.status, l.rule, l.via) for l in output.leaves]
            for output in derived(text, tables, temps).outputs}


def _kinds(text):
    return {name: [(leaf[1], leaf[2], leaf[3]) for leaf in found] for name, found in _outputs(text).items()}


def _missed(text, tables=None):
    return [(m.miss.reason, m.miss.source, m.spec) for m in derived(text, tables).missed]


def _uses(text):
    return sorted((u.kind, u.leaf.table, u.leaf.column) for u in derived(text).usages)


# -- kinds and roles --------------------------------------------------------------------
def test_a_column_as_it_is_passes():
    assert _kinds("SELECT o.amt AS a FROM orders o") == {"a": [("amt", "pass", False)]}


def test_an_operation_is_a_transform():
    assert _kinds("SELECT o.amt + 1 AS t FROM orders o") == {"t": [("amt", "transform", False)]}


def test_a_cast_is_a_transform():
    assert _kinds("SELECT CAST(o.amt AS INT) AS t FROM orders o") == {"t": [("amt", "transform", False)]}


def test_an_aggregate_argument_is_an_aggregate():
    assert _kinds("SELECT SUM(o.amt) AS s FROM orders o") == {"s": [("amt", "aggregate", False)]}


def test_a_window_argument_is_a_window_and_its_keys_are_order_uses():
    text = "SELECT SUM(o.amt) OVER (PARTITION BY o.cust ORDER BY o.id) AS w FROM orders o"

    assert _kinds(text) == {"w": [("amt", "window", False)]}
    assert _uses(text) == [("order_key", "db.orders", "cust"), ("order_key", "db.orders", "id")]


def test_a_literal_output_is_a_constant():
    output = derived("SELECT 1 AS c FROM orders").outputs[0]

    assert (output.constant, output.leaves) == (True, ())


def test_count_star_is_not_a_constant():
    output = derived("SELECT COUNT(*) AS n FROM orders").outputs[0]

    assert output.constant is False


def test_row_number_is_not_a_constant():
    assert derived("SELECT ROW_NUMBER() OVER (ORDER BY id) AS n FROM orders").outputs[0].constant is False


def test_a_case_condition_column_has_the_condition_role():
    assert _kinds("SELECT CASE WHEN o.status = 'x' THEN o.amt END AS k FROM orders o") == {
        "k": [("status", "transform", True), ("amt", "transform", False)]}


def test_an_if_condition_column_has_the_condition_role():
    assert _kinds("SELECT IF(o.status = 'y', 1, 0) AS f FROM orders o") == {"f": [("status", "transform", True)]}


def test_a_filter_where_column_has_the_condition_role_inside_an_aggregate():
    assert _kinds("SELECT SUM(o.amt) FILTER (WHERE o.status = 'p') AS g FROM orders o") == {
        "g": [("amt", "aggregate", False), ("status", "aggregate", True)]}


def test_an_aggregate_order_by_is_an_order_use_not_an_output():
    text = "SELECT STRING_AGG(o.status, ',' ORDER BY o.id) AS s FROM orders o"

    assert _kinds(text) == {"s": [("status", "aggregate", False)]}
    assert _uses(text) == [("order_key", "db.orders", "id")]


def test_a_scalar_subquery_output_reaches_its_column():
    assert _kinds("SELECT (SELECT MAX(c.name) FROM cust c) AS m FROM orders") == {"m": [("name", "aggregate", False)]}


def test_a_column_used_twice_in_one_output_is_one_leaf():
    assert _kinds("SELECT amt + amt * amt AS t FROM orders") == {"t": [("amt", "transform", False)]}


def test_a_template_value_output_is_dynamic():
    output = derived("SELECT {{ var('x') }} AS v FROM orders").outputs[0]

    assert [m.reason for m in output.misses] == ["dynamic"]
    assert output.dynamic is True


# -- usages -----------------------------------------------------------------------------
def test_where_having_and_qualify_columns_are_filters():
    text = "SELECT status FROM orders WHERE amt > 0 GROUP BY status HAVING SUM(id) > 1"

    assert _uses(text) == [("filter", "db.orders", "amt"), ("filter", "db.orders", "id"),
                           ("group_key", "db.orders", "status")]


def test_join_on_columns_are_join_keys():
    assert _uses("SELECT o.amt FROM orders o JOIN cust c ON c.cust = o.cust") == [
        ("join_key", "db.cust", "cust"), ("join_key", "db.orders", "cust")]


def test_a_filter_subquery_column_is_a_filter_use():
    assert ("filter", "db.cust", "name") in _uses("SELECT amt FROM orders WHERE cust IN (SELECT name FROM cust)")


def test_sort_and_distribute_by_are_order_uses():
    assert _uses("SELECT amt FROM orders DISTRIBUTE BY cust SORT BY id") == [
        ("order_key", "db.orders", "cust"), ("order_key", "db.orders", "id")]


def test_a_top_level_order_by_is_an_order_use():
    assert _uses("SELECT amt FROM orders ORDER BY status") == [("order_key", "db.orders", "status")]


def test_a_filter_inside_an_unused_cte_is_not_recorded():
    assert _uses("WITH u AS (SELECT id FROM orders WHERE amt > 0) SELECT name FROM cust") == []


def test_a_filter_inside_a_used_cte_is_recorded():
    assert _uses("WITH u AS (SELECT id FROM orders WHERE amt > 0) SELECT id FROM u") == [
        ("filter", "db.orders", "amt")]


# -- statuses ---------------------------------------------------------------------------
def test_a_documented_list_is_open_and_never_verified():
    tables = {**TABLES, "doc": TableInfo("db.doc", "verified", "name", ("a",), False, True, listed_from="documented")}

    assert _outputs("SELECT d.a FROM doc d", tables)[("a")][0][4:6] == ("inferred", "documented")
    assert _outputs("SELECT d.z FROM doc d", tables)["z"][0][4:6] == ("inferred", "unlisted_column")
    assert _outputs("SELECT z FROM doc", tables)["z"][0][4:6] == ("inferred", "single_source")


def test_an_insert_alias_list_is_not_verified():
    tables = {**TABLES, "ins": TableInfo("db.ins", "verified", "name", ("a",), False, False,
                                         listed_from="insert_alias")}

    assert _outputs("SELECT a FROM ins", tables)["a"][0][4:6] == ("inferred", "insert_alias")


def test_an_open_sql_output_list_is_not_verified():
    tables = {**TABLES, "op": TableInfo("db.op", "verified", "name", ("a",), False, True, listed_from="sql_output")}

    assert _outputs("SELECT a FROM op", tables)["a"][0][4:6] == ("inferred", "open_list")


def test_a_table_matched_by_name_only_is_inferred_table_name():
    tables = {**TABLES, "inf": TableInfo("db.inf", "inferred", "table_name", ("a",), True, False,
                                         listed_from="sql_output")}

    assert _outputs("SELECT i.a FROM inf i", tables)["a"][0][4:6] == ("inferred", "table_name")


def test_a_listed_column_of_an_external_table_qualified_is_verified():
    tables = {**TABLES, "ext": TableInfo("x.ext", "external", None, ("a",), False, True, listed_from="documented")}

    assert _outputs("SELECT e.b FROM ext e", tables)["b"][0][4:6] == ("verified", "qualified")


def test_an_unmatched_table_is_reported_once_where_it_is_read():
    tables = {**TABLES, "bad": TableInfo(None, "unresolved", None, None, False, False, reason="ambiguous",
                                         candidates=("x.bad", "y.bad"))}

    found = derived("SELECT b.x, b.y FROM bad b WHERE b.z = 1", tables).missed

    assert {(m.miss.reason, m.spec, m.start) for m in found} == {("table_ambiguous", "bad", 21)}


# -- set operations, CTEs, temporary tables ---------------------------------------------
def test_union_merges_branches_by_position():
    assert _kinds("SELECT amt FROM orders UNION ALL SELECT name FROM cust") == {
        "amt": [("amt", "pass", False), ("name", "pass", False)]}


def test_union_with_a_constant_branch_keeps_the_column_and_marks_a_constant():
    output = derived("SELECT amt AS v FROM orders UNION ALL SELECT 0 AS v").outputs[0]

    assert [(l.column, l.kind) for l in output.leaves] == [("amt", "pass")]
    assert output.constant is True


def test_union_branches_of_different_widths_are_an_arity_miss():
    outputs = derived("SELECT amt, id FROM orders UNION SELECT name FROM cust").outputs

    assert [[m.reason for m in o.misses] for o in outputs] == [["arity"], ["arity"]]


def test_a_cte_chain_records_its_path_and_keeps_the_strongest_kind():
    text = "WITH a AS (SELECT SUM(amt) AS s FROM orders), b AS (SELECT s + 1 AS t FROM a) SELECT t FROM b"

    assert _outputs(text) == {"t": [("db.orders", "amt", "aggregate", False, "verified", "listed", ("cte:b", "cte:a"))]}


def test_a_cte_column_list_renames_its_outputs():
    assert _outputs("WITH c(x) AS (SELECT amt FROM orders) SELECT x FROM c")["x"][0][:3] == ("db.orders", "amt",
                                                                                             "pass")


def test_a_derived_table_records_its_line_in_the_path():
    assert _outputs("SELECT x\nFROM (SELECT amt AS x\nFROM orders) d")["x"][0][6] == ("subquery:2",)


def test_a_recursive_cte_follows_its_first_branch():
    text = "WITH RECURSIVE r AS (SELECT id AS n FROM orders UNION ALL SELECT n + 1 FROM r) SELECT n FROM r"

    assert {(leaf[0], leaf[1]) for leaf in _outputs(text)["n"]} == {("db.orders", "id")}


def test_a_temporary_table_of_the_same_file_is_passed_through():
    temps = {}
    text = "CREATE TEMP TABLE tt AS SELECT amt AS x FROM orders"
    (statement,) = parse(list(tokens(text)))
    first = derive(text, statement, lambda t: TABLES.get(t.name, external(t.name)), temps)
    temps["tt"] = QueryRelation("temp:tt", list(first.outputs), [])

    assert _outputs("SELECT x FROM tt", temps=temps)["x"][0] == ("db.orders", "amt", "pass", False, "verified",
                                                                 "listed", ("temp:tt",))


def test_a_template_only_cte_is_a_dynamic_table_read():
    assert _missed("WITH s AS ({{ m() }}) SELECT d FROM s") == [("table_dynamic", "cte:s", "cte:s")]


# -- stars ------------------------------------------------------------------------------
def test_a_star_over_a_known_list_expands_each_column_as_star():
    assert _outputs("SELECT * FROM orders") == {
        name: [("db.orders", name, "pass", False, "inferred", "star", ())] for name in ("id", "amt", "status", "cust")}


def test_a_star_except_leaves_out_the_named_columns():
    assert list(_outputs("SELECT * EXCEPT (amt) FROM orders")) == ["id", "status", "cust"]


def test_a_star_replace_reads_the_new_expression():
    assert _outputs("SELECT * REPLACE (amt * 2 AS amt) FROM orders")["amt"][0][2] == "transform"


def test_a_star_rename_renames_the_output():
    assert list(_outputs("SELECT * RENAME (amt AS amount) FROM orders")) == ["id", "amount", "status", "cust"]


def test_a_star_over_an_open_list_is_unknown_star():
    found = derived("SELECT * FROM raw")

    assert found.outputs == ()
    assert _missed("SELECT * FROM raw") == [("unknown_star", "raw.ev", "*")]  # the resolved table
    assert len(found.stars) == 1


def test_a_qualified_star_expands_only_its_source():
    text = "SELECT o.*, r.* FROM orders o, raw r"

    assert list(_outputs(text)) == ["id", "amt", "status", "cust"]
    assert _missed(text) == [("unknown_star", "raw.ev", "r.*")]


def test_a_star_through_a_cte_of_an_unknown_source_passes_names_through():
    found = _outputs("WITH a AS (SELECT * FROM raw) SELECT q FROM a")["q"]

    assert found == [("raw.ev", "q", "pass", False, "inferred", "single_source", ("cte:a",))]


def test_a_known_name_over_a_cte_with_one_unknown_star_is_star_passthrough():
    text = "WITH a AS (SELECT * FROM raw r) SELECT a.q FROM a"

    assert _outputs(text)["q"][0][4:6] == ("inferred", "star_passthrough")


def test_duplicate_output_names_in_a_cte_are_inferred_duplicate_name():
    found = _outputs("WITH a AS (SELECT amt AS x, id AS x FROM orders) SELECT x FROM a")["x"]

    assert {(leaf[1], leaf[4], leaf[5]) for leaf in found} == {("amt", "inferred", "duplicate_name"),
                                                               ("id", "inferred", "duplicate_name")}


def test_a_doubled_star_counts_each_name_once():
    text = "WITH c0 AS (SELECT amt FROM orders), c1 AS (SELECT x.*, y.* FROM c0 x, c0 y), " \
           "c2 AS (SELECT x.*, y.* FROM c1 x, c1 y) SELECT * FROM c2"

    outputs = derived(text).outputs

    assert [o.name for o in outputs] == ["amt"]
    assert all(len(o.leaves) == 1 for o in outputs)


# -- table functions --------------------------------------------------------------------
def test_a_lateral_view_column_comes_from_its_input_as_a_table_function():
    found = _outputs("SELECT item FROM orders LATERAL VIEW explode(status) e AS item")["item"]

    assert [(leaf[1], leaf[2], leaf[4], leaf[5]) for leaf in found] == [("status", "transform", "inferred",
                                                                         "table_function")]


def test_an_unnest_column_comes_from_its_input():
    found = _outputs("SELECT u.v FROM orders o, UNNEST(o.status) u(v)")["v"]

    assert [(leaf[1], leaf[5]) for leaf in found] == [("status", "table_function")]


# -- merge ------------------------------------------------------------------------------
def test_merge_assignments_trace_to_the_source_and_on_is_a_join_key():
    text = "MERGE INTO db.orders t USING cust s ON t.cust = s.cust WHEN MATCHED AND s.region = 'x' THEN " \
           "UPDATE SET status = s.name WHEN NOT MATCHED THEN INSERT (cust, status) VALUES (s.cust, s.name)"

    found = derived(text)

    assert [(o.name, [l.column for l in o.leaves]) for o in found.outputs] == [
        ("status", ["name"]), ("cust", ["cust"]), ("status", ["name"])]
    assert sorted((u.kind, u.leaf.table, u.leaf.column) for u in found.usages) == [
        ("filter", "db.cust", "region"), ("join_key", "db.cust", "cust"), ("join_key", "db.orders", "cust")]
