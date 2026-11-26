"""sqlcol.expressions: the columns an expression uses and the context each sits in."""
import sys

import pytest

from arche_lib.sqlcol.ast import AGG, CONDITION, ORDER, WINDOW
from arche_lib.sqlcol.expressions import ParseError
from arche_lib.sqlcol.parser import Parser
from arche_lib.sqlcol.statements import parse
from arche_lib.sqlcol.tokens import tokens


def _expr(text):
    parser = Parser(list(tokens(text)))
    expr = parser.expr()
    return expr, parser


def _refs(text):
    expr, _ = _expr(text)
    return [(".".join(ref.parts), ref.context) for ref in expr.refs]


def test_a_bare_column_is_one_plain_reference():
    expr, _ = _expr("amt")

    assert [(r.parts, r.display, r.context) for r in expr.refs] == [(("amt",), "amt", 0)]
    assert expr.bare is True


def test_a_qualified_column_keeps_its_parts_lowercased_and_its_display():
    expr, _ = _expr("O.Amt")

    assert [(r.parts, r.display) for r in expr.refs] == [(("o", "amt"), "Amt")]
    assert expr.bare is True


def test_an_operation_is_not_bare_and_keeps_every_column():
    expr, _ = _expr("a + b * c")

    assert [r.parts[0] for r in expr.refs] == ["a", "b", "c"]
    assert expr.bare is False


def test_literals_have_no_references_and_no_calls():
    expr, _ = _expr("1 + 'x' + NULL + TRUE")

    assert expr.refs == () and expr.calls is False


def test_sum_marks_its_argument_as_aggregate():
    assert _refs("SUM(a)") == [("a", AGG)]


def test_count_distinct_marks_its_argument_as_aggregate():
    assert _refs("COUNT(DISTINCT x)") == [("x", AGG)]


def test_count_star_has_no_references_but_is_a_call():
    expr, _ = _expr("COUNT(*)")

    assert expr.refs == ()
    assert expr.calls is True


def test_row_number_is_a_call_and_its_order_key_is_an_order_reference():
    expr, _ = _expr("ROW_NUMBER() OVER (ORDER BY a)")

    assert expr.calls is True
    assert [(r.parts[0], r.context) for r in expr.refs] == [("a", ORDER)]


def test_searched_case_marks_the_when_condition():
    assert _refs("CASE WHEN a > 1 THEN b ELSE c END") == [("a", CONDITION), ("b", 0), ("c", 0)]


def test_simple_case_marks_its_operand_as_a_condition():
    assert _refs("CASE k WHEN 1 THEN b END") == [("k", CONDITION), ("b", 0)]


def test_if_marks_only_its_first_argument_as_a_condition():
    assert _refs("IF(a, b, c)") == [("a", CONDITION), ("b", 0), ("c", 0)]


def test_iff_marks_its_whole_first_argument_as_a_condition():
    assert _refs("IFF(a > 0 AND d, b, c)") == [("a", CONDITION), ("d", CONDITION), ("b", 0), ("c", 0)]


def test_filter_where_marks_an_aggregate_condition():
    assert _refs("SUM(a) FILTER (WHERE b > 0)") == [("a", AGG), ("b", AGG | CONDITION)]


def test_within_group_order_is_an_order_reference():
    assert _refs("PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY a)") == [("a", ORDER)]


def test_an_aggregate_order_by_argument_is_an_order_reference():
    assert _refs("STRING_AGG(a, ',' ORDER BY b)") == [("a", AGG), ("b", AGG | ORDER)]


def test_over_partition_and_order_are_order_references_and_the_argument_a_window():
    refs = _refs("SUM(a) OVER (PARTITION BY p ORDER BY o ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)")

    assert sorted(refs) == [("a", AGG | WINDOW), ("o", ORDER), ("p", ORDER)]


def test_a_named_window_and_ignore_nulls_are_read():
    expr, parser = _expr("FIRST_VALUE(a) IGNORE NULLS OVER w")

    assert [(r.parts[0], r.context) for r in expr.refs] == [("a", WINDOW)]
    assert parser.peek() is None


def test_cast_reads_the_value_and_skips_the_type():
    assert _refs("CAST(a AS DECIMAL(10, 2))") == [("a", 0)]


def test_try_cast_reads_the_value_and_skips_the_type():
    assert _refs("TRY_CAST(a AS INT)") == [("a", 0)]


def test_double_colon_cast_skips_the_type():
    assert _refs("a::varchar(10) || b") == [("a", 0), ("b", 0)]


def test_extract_skips_the_date_part():
    assert _refs("EXTRACT(DAY FROM x)") == [("x", 0)]


def test_dateadd_does_not_read_the_date_part_as_a_column():
    assert _refs("DATEADD(day, 1, x)") == [("x", 0)]


def test_datediff_keeps_both_dates():
    assert _refs("DATEDIFF(day, a, b)") == [("a", 0), ("b", 0)]


def test_date_trunc_keeps_a_column_named_like_a_date_part():
    assert _refs("DATE_TRUNC('month', date)") == [("date", 0)]


def test_interval_literal_is_not_a_column():
    assert _refs("x + INTERVAL '1' DAY") == [("x", 0)]


def test_is_not_distinct_from_joins_two_columns():
    expr, parser = _expr("a IS NOT DISTINCT FROM b")

    assert [r.parts[0] for r in expr.refs] == ["a", "b"]
    assert parser.peek() is None


def test_a_typed_literal_is_not_a_column():
    expr, _ = _expr("DATE '2020-01-01'")

    assert expr.refs == ()


def test_at_time_zone_continues_the_expression():
    (statement,) = parse(list(tokens("SELECT ts AT TIME ZONE 'UTC' AS t FROM x")))

    item = statement.query.body.items[0]
    assert item.name == "t"
    assert statement.tail is False


def test_lambda_parameters_are_not_columns():
    assert _refs("TRANSFORM(arr, e -> e + k)") == [("arr", 0), ("k", 0)]


def test_a_two_parameter_lambda_drops_both_parameters():
    assert _refs("REDUCE(arr, 0, (acc, e) -> acc + e + k)") == [("arr", 0), ("k", 0)]


def test_a_named_argument_name_is_not_a_column():
    assert _refs("FLATTEN(input => x)") == [("x", 0)]


def test_a_snowflake_path_reads_only_the_column():
    assert _refs("col:path.sub") == [("col", 0)]


def test_subscripts_read_the_column_and_the_index():
    assert _refs("arr[i]") == [("arr", 0), ("i", 0)]


def test_a_scalar_subquery_is_kept_as_a_subquery():
    expr, _ = _expr("(SELECT MAX(b) FROM t)")

    assert expr.refs == () and len(expr.subqueries) == 1


def test_an_in_subquery_keeps_the_outer_column_and_the_subquery():
    expr, _ = _expr("a IN (SELECT b FROM t)")

    assert [r.parts for r in expr.refs] == [("a",)]
    assert len(expr.subqueries) == 1


def test_an_exists_subquery_inside_a_case_keeps_the_condition_context():
    expr, _ = _expr("CASE WHEN EXISTS (SELECT 1 FROM t) THEN 1 END")

    assert [context for _, context in expr.subqueries] == [CONDITION]


def test_a_template_value_marks_the_expression_dynamic():
    expr, _ = _expr("{{ var('x') }} + a")

    assert expr.dynamic is True
    assert [r.parts for r in expr.refs] == [("a",)]


def test_a_name_with_a_template_inside_is_dynamic_not_a_column():
    expr, _ = _expr("a_{{ x }}")

    assert expr.dynamic is True and expr.refs == ()


def test_the_expression_stops_before_an_alias():
    expr, parser = _expr("a b")

    assert [r.parts for r in expr.refs] == [("a",)]
    assert parser.peek().text == "b"


def test_the_expression_stops_at_a_clause_keyword():
    _, parser = _expr("a + 1 FROM t")

    assert parser.word() == "FROM"


def test_an_unknown_function_reads_every_argument():
    assert _refs("my_udf(a, b + c)") == [("a", 0), ("b", 0), ("c", 0)]


def test_nesting_up_to_the_limit_is_read():
    expr, _ = _expr("(" * 127 + "a" + ")" * 127)

    assert [r.parts for r in expr.refs] == [("a",)]


def test_nesting_past_the_limit_is_too_deep():
    with pytest.raises(ParseError) as error:
        _expr("(" * 200 + "a" + ")" * 200)

    assert error.value.reason == "too_deep"


def test_deep_nesting_is_too_deep_even_with_a_low_recursion_limit():
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(600)
    try:
        with pytest.raises(ParseError) as error:
            _expr("(" * 5000 + "a" + ")" * 5000)
    finally:
        sys.setrecursionlimit(old)

    assert error.value.reason == "too_deep"


def test_deeply_nested_case_is_too_deep_not_a_recursion_error():
    with pytest.raises(ParseError) as error:
        _expr("CASE WHEN a THEN " * 1000 + "1" + " END" * 1000)

    assert error.value.reason == "too_deep"


def test_an_unclosed_parenthesis_is_a_parse_error():
    with pytest.raises(ParseError):
        _expr("(a + b")


# -- fix round 1 -------------------------------------------------------------------------
def test_chained_arrows_are_read_in_linear_time():
    import time

    text = "TRANSFORM(x, " + " -> ".join("a%d" % i for i in range(20000)) + ")"
    began = time.perf_counter()
    _expr(text)

    assert time.perf_counter() - began < 2.0


def test_a_json_arrow_keeps_the_columns_around_it():
    assert _refs("amt + payload->'k'") == [("amt", 0), ("payload", 0)]


def test_a_json_arrow_with_a_number_keeps_its_column():
    assert _refs("x->0") == [("x", 0)]


def test_a_json_arrow_on_a_lone_column_keeps_it():
    (statement,) = parse(list(tokens("SELECT x->'a' AS j, d FROM t")))

    assert [[r.parts for r in item.expr.refs] for item in statement.query.body.items] == [[("x",)], [("d",)]]


def test_a_lambda_still_drops_its_parameter():
    assert _refs("transform(arr, x -> x + 1)") == [("arr", 0)]


def test_a_qualified_name_before_an_arrow_is_not_a_lambda_parameter():
    assert _refs("t.payload -> k") == [("t.payload", 0), ("k", 0)]


def test_deeply_chained_interval_is_too_deep_not_a_recursion_error():
    with pytest.raises(ParseError) as error:
        _expr("INTERVAL " * 3000 + "1")

    assert error.value.reason == "too_deep"


def test_an_array_constructor_is_not_a_column():
    assert _refs("ARRAY[a, b]") == [("a", 0), ("b", 0)]


def test_a_typed_array_and_struct_constructor_are_not_columns():
    assert _refs("ARRAY<INT64>[a] || STRUCT<x INT64>(b)") == [("a", 0), ("b", 0)]


def test_a_column_named_like_a_constructor_is_still_a_column():
    assert _refs("map + 1") == [("map", 0)]
