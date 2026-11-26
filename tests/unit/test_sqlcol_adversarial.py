"""Hostile SQL ends quickly with a status or a reason, never with an exception."""
import time

import pytest

from arche_lib.column_lineage import parse_file
from arche_lib.sqlcol.derive import derive
from arche_lib.sqlcol.scope import TableInfo

LIMIT_SECONDS = 5
MEGABYTE = 1_000_000


def _lookup(table):
    return TableInfo(table.name, "external", None, None, False, False)


def _run(text):
    """({status:reason: count}, [Derived], seconds) for one file's text."""
    started = time.perf_counter()
    statuses = {}
    derived = []
    temps = {}
    newlines = [i for i, char in enumerate(text) if char == "\n"]
    for item in parse_file(text):
        key = f"{item.status}:{item.reason}"
        statuses[key] = statuses.get(key, 0) + 1
        if item.statement is not None:
            derived.append(derive(text, item.statement, _lookup, temps, newlines))
    return statuses, derived, time.perf_counter() - started


def _cte_chain(count):
    return ("WITH c0 AS (SELECT a FROM t), " + ", ".join(f"c{i} AS (SELECT a FROM c{i - 1})" for i in range(1, count))
            + f" SELECT a FROM c{count - 1}")


CASES = {
    "parens_100k": ("SELECT " + "(" * 100_000 + "a" + ")" * 100_000 + " FROM t", "too_large:too_large"),
    "open_parens_500k": ("SELECT " + "(" * 500_000, "too_large:too_large"),
    "long_line_1mb": (("SELECT " + ", ".join(f"c{i}" for i in range(120_000)) + " FROM t")[:MEGABYTE],
                      "too_large:too_large"),
    "unclosed_quote": ("SELECT 'abc FROM t " + "x " * 400_000, "parsed:None"),
    "unclosed_comment": ("SELECT a /* " + "y " * 400_000, "parsed:None"),
    "unclosed_jinja": (("SELECT {{ a " * 80_000)[:MEGABYTE], "too_large:too_large"),
    "unclosed_double_quotes": ('SELECT "a\n' * 100_000, "too_large:too_large"),
    "case_10k": ("SELECT " + "CASE WHEN a THEN " * 10_000 + "1" + " END" * 10_000 + " FROM t", "parse_error:too_deep"),
    "subqueries_200": ("SELECT x FROM " + "(SELECT x FROM " * 200 + "t" + ") s" * 200, "parse_error:too_deep"),
    "cte_chain_1000": (_cte_chain(1000), "parsed:None"),
    "same_cte_name_10k": ("WITH " + ", ".join("c AS (SELECT a FROM t)" for _ in range(10_000)) + " SELECT a FROM c",
                          "parsed:None"),
    "star_doubling_40": ("WITH c0 AS (SELECT a FROM t), " + ", ".join(
        f"c{i} AS (SELECT x.*, y.* FROM c{i - 1} x, c{i - 1} y)" for i in range(1, 40)) + " SELECT * FROM c39",
        "parsed:None"),
    "items_5000": ("SELECT " + ", ".join(f"a{i}" for i in range(5_000)) + " FROM t", "parse_error:too_many_items"),
    "operators_150k": ("SELECT a" + " + a" * 150_000 + " FROM t", "too_large:too_large"),
    "dollars_500k": ("SELECT " + "$" * 500_000, "too_large:too_large"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_hostile_input_ends_quickly_with_a_reason(name):
    text, expected = CASES[name]

    statuses, _, seconds = _run(text)

    assert statuses == {expected: 1}
    assert seconds < LIMIT_SECONDS


def test_fifty_thousand_statements_take_linear_time():
    statuses, derived, seconds = _run("SELECT a FROM t;" * 50_000)

    assert statuses == {"parsed:None": 50_000}
    assert len(derived) == 50_000
    assert seconds < 10


def test_nine_hundred_thousand_semicolons_are_no_statements():
    statuses, _, seconds = _run(";" * 900_000)

    assert statuses == {}
    assert seconds < LIMIT_SECONDS


def test_a_long_cte_chain_stops_at_the_depth_limit_with_a_reason():
    _, (derived,), _ = _run(_cte_chain(1000))

    assert "too_deep" in {m.miss.reason for m in derived.missed}


def test_a_short_cte_chain_reaches_the_table():
    _, (derived,), _ = _run(_cte_chain(50))

    assert [(leaf.table, leaf.column) for leaf in derived.outputs[0].leaves] == [("t", "a")]


def test_star_doubling_keeps_one_output_per_name():
    _, (derived,), _ = _run(CASES["star_doubling_40"][0])

    assert [output.name for output in derived.outputs] == ["a"]
    assert len(derived.outputs[0].leaves) == 1


def test_the_same_cte_name_ten_thousand_times_reads_one_cte():
    _, (derived,), _ = _run(CASES["same_cte_name_10k"][0])

    assert [(leaf.table, leaf.column) for leaf in derived.outputs[0].leaves] == [("t", "a")]


def test_subqueries_up_to_sixty_three_deep_are_read():
    text = "SELECT " + "(SELECT " * 63 + "a" + ")" * 63

    statuses, _, _ = _run(text)

    assert statuses == {"parsed:None": 1}


def test_expansion_past_the_column_limit_is_cut_at_the_next_item():
    names = ", ".join(f"a{i}" for i in range(1_500))
    text = f"WITH c AS (SELECT {names} FROM t) SELECT x.*, y.*, z.* FROM c x, c y, c z"

    _, (derived,), _ = _run(text)

    assert "too_many_columns" in {m.miss.reason for m in derived.missed}


def test_expansion_never_passes_the_column_limit():
    names = ", ".join(f"a{i}" for i in range(1_500))
    text = f"WITH c AS (SELECT {names} FROM t) SELECT x.*, y.* FROM c x, c y"

    _, (derived,), _ = _run(text)

    assert len(derived.outputs) <= 2_000
    assert "too_many_columns" in {m.miss.reason for m in derived.missed}
