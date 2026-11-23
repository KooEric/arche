from arche_lib.langs import sql
from arche_lib.langs.sql_tables import mask, tables


def _found(source):
    return [(item["access"], item["table"]) for item in tables("q.sql", source)["tables"]]


def test_mask_blanks_comments_strings_and_jinja_tags_and_keeps_newlines():
    masked, unterminated = mask("a -- x 'q\nb /* y\nz */ c 'it''s' d {% if x %}e{# n #}f")

    assert masked == "a        \nb     \n     c         d           e       f"
    assert unterminated is False


def test_an_unterminated_string_marks_the_file_degraded_and_keeps_what_was_read():
    result = tables("q.sql", "select * from q.r where s = 'unterminated")

    assert [(item["access"], item["table"]) for item in result["tables"]] == [("read", "q.r")]
    assert result["degraded"] is True


def test_a_record_carries_its_line_and_quote():
    record = tables("q.sql", "SELECT 1\n  FROM   acct.ledger  -- note\n")["tables"][0]

    assert record == {"table": "acct.ledger", "access": "read", "line": 2, "quote": "FROM   acct.ledger  -- note",
                      "dynamic": False, "temp": False}


def test_from_and_join_read_tables_and_drop_aliases():
    assert _found("SELECT *\nFROM a.b x\nJOIN c.d AS y ON 1=1") == [("read", "a.b"), ("read", "c.d")]


def test_comma_lists_subqueries_and_table_functions():
    source = ("select * from a.x t1, b.y, (select 1 from c.z) q left join `d`.`W` as w on 1=1 "
              "join lateral unnest(arr) u on true")

    assert _found(source) == [("read", "a.x"), ("read", "b.y"), ("read", "c.z"), ("read", "d.w")]
    assert _found("select * from generate_series(1, 3) g, t.real") == []


def test_from_inside_a_function_call_is_not_a_table():
    source = "select substring(n from 1), trim(both ' ' from n) from e.t where d in (select d from f.g)"

    assert _found(source) == [("read", "e.t"), ("read", "f.g")]


def test_cte_names_are_not_tables():
    source = "with a as (select 1 from x.one), b as (select * from a) select * from b join a on 1=1"

    assert _found(source) == [("read", "x.one")]


def test_lateral_view_is_not_a_table():
    assert _found("select * from t.base lateral view explode(arr) e as item") == [("read", "t.base")]


def test_hive_from_first_insert():
    source = "FROM src.t a INSERT OVERWRITE TABLE dst.x PARTITION (dt='1') SELECT a.* WHERE a.k > 0"

    assert _found(source) == [("read", "src.t"), ("write", "dst.x")]


def test_create_insert_delete_and_merge_write_tables():
    source = (
        "CREATE EXTERNAL TABLE IF NOT EXISTS w.h (a int) USING parquet;\n"
        "CREATE OR REPLACE VIEW v.v AS SELECT * FROM w.h;\n"
        "DROP TABLE IF EXISTS w.old;\n"
        "DELETE FROM w.h WHERE a = 1;\n"
        "INSERT OVERWRITE LOCAL DIRECTORY '/x' SELECT * FROM w.h"
    )

    assert _found(source) == [("write", "w.h"), ("read", "w.h"), ("write", "v.v")]
    assert _found("MERGE INTO tgt.t AS t USING src.s AS s ON t.k = s.k WHEN MATCHED THEN UPDATE SET v = s.v") == [
        ("read", "src.s"), ("write", "tgt.t")]


def test_temporary_tables_are_marked():
    source = ("CREATE TEMP VIEW s.tv AS SELECT 1;\ncreate temporary table tmp_a as select * from a.b;\n"
              "select * from tmp_a;\nselect * from a.b")

    assert [(item["line"], item["access"], item["table"], item["temp"]) for item in tables("q.sql", source)["tables"]] == [
        (1, "write", "s.tv", True), (2, "read", "a.b", False), (2, "write", "tmp_a", True), (3, "read", "tmp_a", False)]


def test_names_are_unquoted_lowercased_and_limited_to_three_parts():
    assert _found('select * from `Acct`.`Ledger` join "S"."T" on 1=1 join 1abc on 1=1') == [
        ("read", "acct.ledger"), ("read", "s.t")]
    assert _found("select * from cat.sch.t3 join a.b.c.d on 1=1") == [("read", "cat.sch.t3")]


def test_run_time_values_become_placeholders():
    source = "select * from ${hiveconf:db}.t1 join $db.t2 on 1=1 join tmp_${run} on 1=1"
    found = {item["table"]: item["dynamic"] for item in tables("q.sql", source)["tables"]}

    assert found == {"{}.t1": False, "{}.t2": False, "tmp_{}": True}


def test_jinja_blocks_are_read_through():
    source = "{% for d in days %} insert into a.b select * from c.d_{{ d }} {% endfor %}"

    assert [(item["access"], item["table"], item["dynamic"]) for item in tables("q.sql", source)["tables"]] == [
        ("read", "c.d_{}", True), ("write", "a.b", False)]


def test_the_sql_adapter_reports_tables_and_degraded():
    assert [item["table"] for item in sql.extract("q.sql", "select * from a.b")["tables"]] == ["a.b"]
    assert "degraded" not in sql.extract("q.sql", "select * from a.b")
    assert sql.extract("q.sql", "select 'open")["degraded"] is True
