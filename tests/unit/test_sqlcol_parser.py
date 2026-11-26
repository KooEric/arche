"""sqlcol.parser and sqlcol.statements: queries, FROM, clauses and statement heads."""
import pytest

from arche_lib.sqlcol.ast import FunctionSource, Query, QuerySource, Select, SetOp, TableSource, Values
from arche_lib.sqlcol.expressions import ParseError
from arche_lib.sqlcol.statements import parse
from arche_lib.sqlcol.tokens import tokens


def _parse(text):
    return parse(list(tokens(text)))


def _one(text):
    (statement,) = _parse(text)
    return statement


def _select(text):
    body = _one(text).query.body
    while isinstance(body, Query):
        body = body.body
    return body


def _reason(text):
    with pytest.raises(ParseError) as error:
        _parse(text)
    return error.value.reason


def _items(text):
    return [item.name for item in _select(text).items]


def _tables(text):
    return [(s.table.name, s.alias) for s in _select(text).sources if isinstance(s, TableSource)]


# -- queries ---------------------------------------------------------------------------
def test_a_select_is_a_query_statement():
    statement = _one("SELECT a FROM t")

    assert (statement.kind, statement.target, statement.tail) == ("query", None, False)


def test_with_keeps_each_cte_in_order():
    query = _one("WITH a AS (SELECT x FROM t), b AS (SELECT x FROM a) SELECT x FROM b").query

    assert [cte.name for cte in query.ctes] == ["a", "b"]
    assert all(cte.query is not None for cte in query.ctes)


def test_a_cte_column_list_is_kept():
    (cte,) = _one("WITH c(p, q) AS (SELECT 1, 2) SELECT p FROM c").query.ctes

    assert cte.columns == ("p", "q")


def test_recursive_marks_the_ctes():
    (cte,) = _one("WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r) SELECT n FROM r").query.ctes

    assert cte.recursive is True
    assert isinstance(cte.query.body, SetOp)


def test_materialized_cte_is_read():
    (cte,) = _one("WITH c AS NOT MATERIALIZED (SELECT 1 AS x) SELECT x FROM c").query.ctes

    assert cte.name == "c"


def test_set_operations_keep_every_branch():
    body = _one("SELECT a FROM t UNION SELECT b FROM u INTERSECT SELECT c FROM v EXCEPT SELECT d FROM w "
                "MINUS SELECT e FROM z UNION ALL SELECT f FROM y").query.body

    assert isinstance(body, SetOp)
    assert len(body.branches) == 6


def test_a_parenthesized_query_branch_is_read():
    body = _one("(SELECT a FROM t) UNION (SELECT b FROM u)").query.body

    assert isinstance(body, SetOp) and len(body.branches) == 2


def test_union_by_name_is_unsupported():
    assert _reason("SELECT a FROM t UNION BY NAME SELECT b FROM u") == "by_name"


def test_order_limit_and_offset_end_the_query():
    statement = _one("SELECT a FROM t ORDER BY a DESC NULLS LAST, b LIMIT 10 OFFSET 5")

    assert len(statement.query.orders) == 2
    assert statement.tail is False


def test_fetch_first_ends_the_query():
    assert _one("SELECT a FROM t FETCH FIRST 10 ROWS ONLY").tail is False


def test_limit_with_a_comma_is_read():
    assert _one("SELECT a FROM t LIMIT 5, 10").tail is False


def test_distinct_on_is_skipped():
    assert _items("SELECT DISTINCT ON (a) a, b FROM t") == ["a", "b"]


def test_top_n_is_skipped():
    assert _items("SELECT TOP 10 a FROM t") == ["a"]


def test_items_are_named_with_as_without_as_or_by_their_column():
    assert _items("SELECT a AS x, b y, t.c, a + 1 FROM t") == ["x", "y", "c", None]


def test_a_quoted_alias_keeps_its_display():
    item = _select('SELECT a AS "Total" FROM t').items[0]

    assert (item.name, item.display) == ("total", "Total")


def test_a_trailing_comma_before_from_is_accepted():
    assert _items("SELECT a, b, FROM t") == ["a", "b"]


def test_star_and_qualified_star_items():
    items = _select("SELECT *, t.*, s.x FROM t, s").items

    assert [item.star.qualifier if item.star else item.name for item in items] == [None, ("t",), "x"]


def test_star_except_and_exclude_lists():
    assert _select("SELECT * EXCEPT (a, b) FROM t").items[0].star.except_ == ("a", "b")
    assert _select("SELECT * EXCLUDE (a) FROM t").items[0].star.except_ == ("a",)
    assert _select("SELECT * EXCLUDE a FROM t").items[0].star.except_ == ("a",)


def test_star_replace_keeps_the_expression_per_name():
    star = _select("SELECT * REPLACE (a + 1 AS a) FROM t").items[0].star

    assert [name for name, _ in star.replace] == ["a"]
    assert [r.parts for r in star.replace[0][1].refs] == [("a",)]


def test_star_rename_with_and_without_parentheses():
    assert _select("SELECT * RENAME (a AS b, c AS D) FROM t").items[0].star.rename == (("a", "b", "b"),
                                                                                        ("c", "d", "D"))
    assert _select("SELECT * RENAME a AS b FROM t").items[0].star.rename == (("a", "b", "b"),)


def test_too_many_items_is_a_parse_error():
    assert _reason("SELECT " + ", ".join(f"a{i}" for i in range(2_100)) + " FROM t") == "too_many_items"


# -- FROM ------------------------------------------------------------------------------
def test_a_table_alias_with_and_without_as():
    assert _tables("SELECT x FROM db.t AS a, db.u b, db.v") == [("db.t", "a"), ("db.u", "b"), ("db.v", "v")]


def test_a_table_column_alias_list():
    (source,) = _select("SELECT x FROM t a(p, q)").sources

    assert (source.alias, source.columns) == ("a", ("p", "q"))


def test_a_derived_table_keeps_its_alias_and_column_list():
    (source,) = _select("SELECT x FROM (SELECT 1, 2) d(x, y)").sources

    assert isinstance(source, QuerySource)
    assert (source.alias, source.columns) == ("d", ("x", "y"))


def test_nested_join_parentheses_bring_every_table():
    select = _select("SELECT x FROM (a JOIN b ON a.k = b.k) LEFT JOIN c USING (k)")

    assert [s.table.name for s in select.sources] == ["a", "b", "c"]
    assert select.using == (("k",),)
    assert len(select.joins) == 1


def test_every_join_kind_is_read():
    select = _select("SELECT x FROM a INNER JOIN b ON 1=1 LEFT OUTER JOIN c ON 1=1 RIGHT JOIN d ON 1=1 "
                     "FULL OUTER JOIN e ON 1=1 CROSS JOIN f NATURAL JOIN g LEFT SEMI JOIN h ON 1=1 "
                     "LEFT ANTI JOIN i ON 1=1")

    assert [s.table.name for s in select.sources] == list("abcdefghi")
    assert len(select.joins) == 6


def test_cross_apply_is_a_table_function():
    sources = _select("SELECT x FROM a CROSS APPLY f(a.x) z").sources

    assert isinstance(sources[1], FunctionSource) and sources[1].alias == "z"


def test_lateral_view_outer_is_a_function_source_with_its_columns():
    sources = _select("SELECT item, pos FROM t LATERAL VIEW OUTER posexplode(arr) e AS pos, item").sources

    function = sources[1]
    assert isinstance(function, FunctionSource)
    assert (function.alias, function.columns) == ("e", ("pos", "item"))
    assert [r.parts for r in function.inputs.refs] == [("arr",)]


def test_unnest_with_ordinality_and_a_column_alias():
    (function,) = _select("SELECT v, o FROM UNNEST(arr) WITH ORDINALITY AS u(v, o)").sources

    assert (function.alias, function.columns) == ("u", ("v", "o"))


def test_a_table_function_reads_its_named_arguments():
    function = _select("SELECT value FROM t, TABLE(FLATTEN(input => t.j)) f").sources[1]

    assert isinstance(function, FunctionSource)
    assert [r.parts for r in function.inputs.refs] == [("t", "j")]


def test_lateral_subquery_is_a_derived_table():
    sources = _select("SELECT x FROM t, LATERAL (SELECT t.a AS x) s").sources

    assert isinstance(sources[1], QuerySource) and sources[1].alias == "s"


def test_values_in_from_keeps_its_alias_and_column_list():
    (source,) = _select("SELECT a FROM (VALUES (1, 2), (3, 4)) v(a, b)").sources

    assert (source.alias, source.columns) == ("v", ("a", "b"))


def test_values_in_from_without_parentheses_keeps_its_alias():
    (source,) = _select("SELECT a FROM VALUES (1, 2) AS v(a, b)").sources

    assert isinstance(source.query.body, Values)
    assert (source.alias, source.columns) == ("v", ("a", "b"))


def test_table_suffixes_are_skipped():
    for suffix in ("TABLESAMPLE (10 PERCENT)", "SAMPLE (5)", "AT (TIMESTAMP => '2020')", "BEFORE (STATEMENT => 'x')",
                   "WITH (NOLOCK)", "FOR SYSTEM_TIME AS OF '2020'"):
        statement = _one(f"SELECT a FROM t {suffix} WHERE b = 1")
        assert statement.tail is False, suffix
        assert len(statement.query.body.where) == 1, suffix


def test_a_dbt_ref_source_keeps_the_call():
    (source,) = _select("SELECT * FROM {{ ref('m', v=2) }}").sources

    assert source.table.dbt == ("ref", ("m",), "2")
    assert source.table.name is None


def test_a_dynamic_table_name_is_marked():
    (source,) = _select("SELECT a FROM ledger.daily_{{ ds }}").sources

    assert (source.table.name, source.table.dynamic) == ("ledger.daily_{}", True)


def test_a_template_only_derived_table_is_a_dynamic_source():
    (source,) = _select("SELECT a FROM ( {{ macro() }} ) x").sources

    assert (source.table.name, source.table.dynamic, source.alias) == (None, True, "x")


def test_pivot_is_unsupported():
    assert _reason("SELECT a FROM t PIVOT (SUM(a) FOR b IN (1))") == "pivot"
    assert _reason("SELECT a FROM t UNPIVOT (v FOR k IN (a))") == "pivot"


# -- clauses ---------------------------------------------------------------------------
def test_where_having_and_qualify_are_conditions():
    select = _select("SELECT a FROM t WHERE c > 0 GROUP BY a HAVING SUM(b) > 1 "
                     "QUALIFY ROW_NUMBER() OVER (ORDER BY d) = 1")

    assert len(select.where) == 3
    assert [[r.parts for r in g.refs] for g in select.groups] == [[("a",)]]


def test_group_by_a_number_has_no_references():
    (group,) = _select("SELECT a, SUM(b) FROM t GROUP BY 1").groups

    assert group.refs == ()


def test_group_by_all_has_no_keys():
    statement = _one("SELECT a FROM t GROUP BY ALL")

    assert statement.query.body.groups == () and statement.tail is False


def test_group_by_rollup_and_cube_read_their_columns():
    assert [r.parts for r in _select("SELECT a FROM t GROUP BY ROLLUP (a, b)").groups[0].refs] == [("a",), ("b",)]
    assert [r.parts for r in _select("SELECT a FROM t GROUP BY CUBE (a)").groups[0].refs] == [("a",)]


def test_group_by_with_rollup_is_read():
    statement = _one("SELECT a FROM t GROUP BY a WITH ROLLUP")

    assert statement.tail is False


def test_group_by_grouping_sets_reads_its_columns():
    statement = _one("SELECT a FROM t GROUP BY GROUPING SETS ((a), (b))")

    assert statement.tail is False
    assert sorted(r.parts for g in statement.query.body.groups for r in g.refs) == [("a",), ("b",)]


def test_a_named_window_is_an_order_use():
    select = _select("SELECT SUM(a) OVER w FROM t WINDOW w AS (PARTITION BY p ORDER BY o)")

    assert [sorted(r.parts for r in o.refs) for o in select.orders] == [[("o",), ("p",)]]


def test_distribute_sort_and_cluster_by_are_order_uses():
    assert len(_select("SELECT a FROM t DISTRIBUTE BY a SORT BY b").orders) == 2
    assert len(_select("SELECT a FROM t CLUSTER BY a").orders) == 1


def test_left_over_tokens_mark_the_statement_tail():
    assert _one("SELECT a FROM t x y z").tail is True


def test_select_into_is_unsupported():
    assert _reason("SELECT a INTO b FROM t") == "select_into"


def test_a_missing_table_is_a_parse_error():
    assert _reason("SELECT a FROM") == "expected table"


# -- statements ------------------------------------------------------------------------
def test_insert_into_has_a_target_and_a_query():
    statement = _one("INSERT INTO db.t SELECT a FROM s")

    assert (statement.kind, statement.target.name, statement.columns) == ("insert", "db.t", None)


def test_insert_with_a_column_list():
    assert _one("INSERT INTO db.t (a, B) SELECT x, y FROM s").columns == (("a", "a"), ("b", "B"))


def test_insert_overwrite_with_static_and_dynamic_partitions():
    statement = _one("INSERT OVERWRITE TABLE db.t PARTITION (dt='2020', hr) SELECT a, h FROM s")

    assert [c.column for c in statement.constants] == ["dt"]
    assert statement.partitions == (("hr", "hr"),)


def test_insert_if_not_exists_is_read():
    assert _one("INSERT OVERWRITE TABLE t PARTITION (dt='1') IF NOT EXISTS SELECT a FROM s").tail is False


def test_with_before_insert_moves_the_ctes_into_the_query():
    statement = _one("WITH c AS (SELECT a FROM s) INSERT INTO db.t SELECT a FROM c")

    assert statement.kind == "insert"
    assert [cte.name for cte in statement.query.ctes] == ["c"]


def test_insert_all_is_unsupported():
    assert _reason("INSERT ALL INTO a SELECT 1") == "multi_table_insert"


def test_insert_by_name_is_unsupported():
    assert _reason("INSERT INTO t BY NAME SELECT a FROM s") == "by_name"


def test_insert_default_values_is_unsupported():
    assert _reason("INSERT INTO t DEFAULT VALUES") == "default_values"


def test_insert_into_a_dbt_ref_keeps_the_call():
    statement = _one("INSERT INTO {{ ref('m') }} SELECT 1")

    assert statement.target.dbt == ("ref", ("m",), None)


def test_create_table_as_is_a_create():
    statement = _one("CREATE OR REPLACE TABLE db.c AS SELECT a FROM s")

    assert (statement.kind, statement.target.name, statement.temp, statement.columns) == ("create", "db.c", False,
                                                                                            None)


def test_create_temp_table_is_temporary():
    assert _one("CREATE TEMP TABLE tt AS SELECT a FROM s").temp is True
    assert _one("CREATE TEMPORARY TABLE tt AS SELECT a FROM s").temp is True


def test_create_view_as_is_a_create():
    statement = _one("CREATE VIEW IF NOT EXISTS v AS WITH c AS (SELECT 1 AS x) SELECT x FROM c")

    assert (statement.kind, statement.target.name) == ("create", "v")


def test_create_table_with_a_column_list_as_query():
    assert _one("CREATE TABLE db.c (a, b) AS SELECT x, y FROM s").columns == (("a", "a"), ("b", "b"))


def test_create_table_with_hive_options_before_as():
    statement = _one("CREATE TABLE db.h STORED AS ORC TBLPROPERTIES ('a'='b') AS SELECT a FROM s")

    assert (statement.kind, statement.tail) == ("create", False)


def test_ddl_lists_column_names_and_skips_constraints():
    statement = _one("CREATE TABLE db.d (id INT PRIMARY KEY, name VARCHAR(10), m MAP<STRING, INT>, "
                     "PRIMARY KEY (id), CONSTRAINT c UNIQUE (name))")

    assert statement.kind == "ddl"
    assert [name for name, _ in statement.columns] == ["id", "name", "m"]


def test_ddl_adds_partitioned_by_columns():
    statement = _one("CREATE TABLE db.p (id INT) PARTITIONED BY (dt STRING) STORED AS ORC")

    assert [name for name, _ in statement.columns] == ["id", "dt"]


def test_create_like_function_and_bare_view_are_ignored():
    assert _parse("CREATE TABLE x LIKE y") == []
    assert _parse("CREATE FUNCTION f() RETURNS INT") == []
    assert _parse("CREATE VIEW v") == []


def test_merge_reads_updates_inserts_conditions_and_delete():
    statement = _one("MERGE INTO db.t AS t USING db.s AS s ON t.id = s.id "
                     "WHEN MATCHED AND s.flag THEN UPDATE SET t.a = s.a, b = s.b "
                     "WHEN NOT MATCHED THEN INSERT (id, a) VALUES (s.id, s.a) "
                     "WHEN MATCHED THEN DELETE")

    merge = statement.merge
    assert (statement.kind, statement.target.name, merge.target_alias) == ("merge", "db.t", "t")
    assert [a.column for a in merge.updates] == ["a", "b"]
    assert [a.column for a in merge.inserts] == ["id", "a"]
    assert len(merge.conditions) == 1


def test_merge_using_a_subquery_is_read():
    statement = _one("MERGE INTO t USING (SELECT id FROM s) src ON t.id = src.id "
                     "WHEN MATCHED THEN UPDATE SET id = src.id")

    assert isinstance(statement.merge.source, QuerySource)


def test_merge_insert_values_of_the_wrong_length_is_an_arity_error():
    assert _reason("MERGE INTO t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT (id, a) VALUES (s.id)") == \
        "arity"


def test_merge_insert_without_a_column_list_is_an_arity_error():
    assert _reason("MERGE INTO t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT VALUES (s.id)") == "arity"


def test_merge_star_forms_are_unsupported():
    assert _reason("MERGE INTO t USING s ON 1=1 WHEN MATCHED THEN UPDATE SET *") == "merge_star"
    assert _reason("MERGE INTO t USING s ON 1=1 WHEN NOT MATCHED THEN INSERT *") == "merge_star"


def test_hive_multi_insert_gives_one_statement_per_insert_reading_the_shared_source():
    found = _parse("FROM src s INSERT OVERWRITE TABLE a SELECT s.x WHERE s.k > 0 "
                   "INSERT INTO TABLE b PARTITION (dt='1') SELECT s.y")

    assert [(s.kind, s.target.name) for s in found] == [("insert", "a"), ("insert", "b")]
    assert all(s.query.body.sources[0].table.name == "src" for s in found)
    assert [c.column for c in found[1].constants] == ["dt"]


def test_hive_from_without_insert_is_a_parse_error():
    assert _reason("FROM src s WHERE 1") == "expected INSERT"


def test_a_macro_body_starting_with_a_parenthesis_is_ignored():
    assert _parse("(a + b) * c") == []


def test_a_parenthesized_query_is_still_a_query():
    assert _one("((SELECT 1))").kind == "query"


def test_config_before_and_a_macro_after_the_statement_are_ignored():
    statement = _one("{{ config(materialized='table') }} SELECT a FROM t {{ log('x') }}")

    assert statement.tail is False
    assert _items("{{ config(materialized='table') }} SELECT a FROM t {{ log('x') }}") == ["a"]


def test_a_template_only_statement_is_nothing():
    assert _parse("{{ config(x=1) }}") == []


def test_a_template_only_cte_body_has_no_query():
    (cte,) = _one("WITH spine AS ({{ dbt.date_spine('day') }}) SELECT d FROM spine").query.ctes

    assert cte.query is None


def test_procedural_statements_are_errors():
    for text in ("BEGIN", "DECLARE x INT", "CALL p()", "EXECUTE IMMEDIATE 'x'"):
        assert _reason(text) == "procedural", text


def test_other_statements_are_skipped():
    for text in ("UPDATE t SET a = 1", "ALTER TABLE t ADD c INT", "DELETE FROM t", "DROP TABLE t", "SET x = 1",
                 "USE db", "GRANT SELECT ON t TO r"):
        assert _parse(text) == [], text


def test_values_statement_is_a_query():
    body = _one("VALUES (1, 2), (3, 4)").query.body

    assert isinstance(body, Values) and len(body.rows) == 2


def test_select_body_is_a_select_node():
    assert isinstance(_select("SELECT 1"), Select)


# -- fix round 1 -------------------------------------------------------------------------
def _ddl_names(text):
    return [name for name, _ in _one(text).columns]


def test_a_check_with_greater_than_keeps_the_next_column():
    assert _ddl_names("CREATE TABLE t (a INT CHECK (a > 0), b INT)") == ["a", "b"]


def test_a_check_with_less_than_keeps_the_next_column():
    assert _ddl_names("CREATE TABLE t (a INT CHECK (a < 0), b INT)") == ["a", "b"]


def test_struct_array_and_map_column_types_keep_the_right_names():
    text = "CREATE TABLE t (s STRUCT<a INT, b STRING>, xs ARRAY<STRUCT<c INT, d ARRAY<INT>>>, m MAP<STRING, INT>, z INT)"

    assert _ddl_names(text) == ["s", "xs", "m", "z"]


def test_top_with_parentheses_is_skipped():
    statement = _one("SELECT TOP (10) a FROM t")

    assert [item.name for item in statement.query.body.items] == ["a"]
    assert statement.tail is False


def test_top_with_a_number_is_skipped():
    assert _items("SELECT TOP 10 a, b FROM t") == ["a", "b"]


_PREFIX_SOURCES = [
    "INSERT INTO db.t PARTITION (dt = '1', h) (a, b) SELECT a, b + INTERVAL 1 DAY FROM s",
    "CREATE TABLE db.t AS SELECT a, CAST(b AS INT) AS b FROM s",
    "CREATE TABLE t (a INT CHECK (a > 0), s STRUCT<x INT, y STRING>, b INT) PARTITIONED BY (dt STRING)",
    "MERGE INTO t AS x USING (SELECT * FROM s) AS y ON x.id = y.id WHEN MATCHED AND y.d = 1 THEN DELETE "
    "WHEN MATCHED THEN UPDATE SET x.a = y.a, b = y.b WHEN NOT MATCHED BY TARGET THEN INSERT (id, a) VALUES (y.id, y.a)",
    "WITH c (k) AS (SELECT a FROM t) SELECT k, ARRAY[k], TRANSFORM(k, e -> e + 1) FROM c ORDER BY k LIMIT 3",
    "FROM s INSERT OVERWRITE TABLE a SELECT x WHERE y > 0 INSERT INTO TABLE b SELECT TOP (1) z GROUP BY z",
]


@pytest.mark.parametrize("text", _PREFIX_SOURCES)
def test_every_prefix_of_a_statement_parses_or_is_a_parse_error(text):
    toks = list(tokens(text))
    for end in range(len(toks) + 1):
        try:
            parse(toks[:end])
        except ParseError:
            pass


# -- fix round 2 -------------------------------------------------------------------------
def test_top_percent_is_skipped():
    statement = _one("SELECT TOP 10 PERCENT a FROM t")

    assert [item.name for item in statement.query.body.items] == ["a"]
    assert [r.parts for r in statement.query.body.items[0].expr.refs] == [("a",)]


def test_top_with_ties_is_skipped():
    assert _items("SELECT TOP (5) WITH TIES a, b FROM t ORDER BY a") == ["a", "b"]
