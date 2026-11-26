"""sqlcol.tokens: one forward pass over SQL text, split into statements."""
import time

from arche_lib.sqlcol.tokens import DBT, NUMBER, OP, QUOTED, STRING, WORD, statements, tokens


def _kinds(text):
    return [(token.kind, token.text) for token in tokens(text)]


def test_tokens_keep_their_start_and_end_positions():
    found = list(tokens("SELECT a\n  FROM t"))

    assert [(t.text, t.start, t.end) for t in found] == [("SELECT", 0, 6), ("a", 7, 8), ("FROM", 11, 15),
                                                         ("t", 16, 17)]


def test_positions_point_back_into_the_original_text():
    text = "select o.amt -- note\n from /* c */ x.orders o"

    for token in tokens(text):
        if token.kind == WORD:
            assert text[token.start:token.end] == token.text


def test_double_quotes_are_names_when_the_file_has_no_backtick():
    assert _kinds('SELECT "Order Id" FROM t') == [(WORD, "SELECT"), (QUOTED, "Order Id"), (WORD, "FROM"), (WORD, "t")]


def test_double_quotes_are_strings_when_the_file_uses_backticks():
    assert _kinds('SELECT "x", `y` FROM t') == [(WORD, "SELECT"), (STRING, ""), (OP, ","), (QUOTED, "y"),
                                                (WORD, "FROM"), (WORD, "t")]


def test_a_quote_not_closed_on_its_line_is_an_operator():
    assert _kinds('"open\nx') == [(OP, '"'), (WORD, "open"), (WORD, "x")]


def test_two_character_operators_are_one_token():
    assert _kinds("a::int -> b || c => d") == [(WORD, "a"), (OP, "::"), (WORD, "int"), (OP, "->"), (WORD, "b"),
                                               (OP, "||"), (WORD, "c"), (OP, "=>"), (WORD, "d")]


def test_comparison_operators_are_one_token():
    assert [t.text for t in tokens("<= >= <> != ==")] == ["<=", ">=", "<>", "!=", "=="]


def test_numbers_with_decimals_and_exponents():
    assert _kinds("1 1.5 .5 1e10 2E-3") == [(NUMBER, "1"), (NUMBER, "1.5"), (NUMBER, ".5"), (NUMBER, "1e10"),
                                            (NUMBER, "2E-3")]


def test_a_word_that_starts_with_a_digit_is_a_word():
    assert _kinds("1abc 2_x") == [(WORD, "1abc"), (WORD, "2_x")]


def test_strings_keep_no_content_and_doubled_quotes_stay_inside():
    found = list(tokens("'it''s' x"))

    assert [(t.kind, t.text) for t in found] == [(STRING, ""), (WORD, "x")]
    assert (found[0].start, found[0].end) == (0, 7)


def test_a_backslash_escapes_a_quote_inside_a_string():
    assert _kinds("'a\\'b' x") == [(STRING, ""), (WORD, "x")]


def test_a_select_inside_a_string_is_not_read():
    assert _kinds("'SELECT a FROM b' c") == [(STRING, ""), (WORD, "c")]


def test_a_ref_call_is_one_dbt_token_with_its_arguments():
    (token,) = tokens("{{ ref('orders') }}")

    assert (token.kind, token.text, token.args, token.version) == (DBT, "ref", ("orders",), None)


def test_a_ref_call_keeps_its_version_argument():
    (token,) = tokens("{{ ref('m', v=2) }}")

    assert (token.kind, token.args, token.version) == (DBT, ("m",), "2")


def test_a_ref_call_with_a_package_keeps_both_arguments():
    (token,) = tokens("{{ ref('pkg', 'm') }}")

    assert token.args == ("pkg", "m")


def test_a_source_call_is_one_dbt_token():
    (token,) = tokens("{{ source('raw', 'events') }}")

    assert (token.kind, token.text, token.args) == (DBT, "source", ("raw", "events"))


def test_a_ref_with_whitespace_control_dashes_is_still_a_dbt_token():
    (token,) = tokens("{{- ref('m') -}}")

    assert (token.kind, token.args) == (DBT, ("m",))


def test_a_ref_with_a_non_literal_argument_has_no_arguments():
    (token,) = tokens("{{ ref(name) }}")

    assert (token.kind, token.args) == (DBT, None)


def test_a_ref_glued_to_a_word_is_a_run_time_value():
    assert _kinds("x{{ ref('m') }}") == [(WORD, "x{}")]


def test_a_template_glued_to_a_name_becomes_braces_inside_the_word():
    assert _kinds("ledger.daily_{{ ds }}") == [(WORD, "ledger"), (OP, "."), (WORD, "daily_{}")]


def test_a_standalone_template_is_a_braces_word():
    assert _kinds("{{ var('x') }}") == [(WORD, "{}")]


def test_dollar_brace_and_dollar_name_values_are_braces():
    assert _kinds("${x} $name a${y}b") == [(WORD, "{}"), (WORD, "{}"), (WORD, "a{}b")]


def test_a_dollar_brace_not_closed_on_its_line_is_not_a_value():
    assert (WORD, "{}") not in _kinds("${x\n}")


def test_the_sql_inside_an_if_block_stays_and_both_branches_are_kept():
    assert _kinds("{% if x %}a{% else %}b{% endif %}") == [(WORD, "a"), (WORD, "b")]


def test_comments_and_jinja_comments_are_dropped():
    assert _kinds("a -- line\n/* block */ b {# note #} c") == [(WORD, "a"), (WORD, "b"), (WORD, "c")]


def test_a_select_inside_a_comment_is_ignored():
    assert _kinds("/* SELECT x FROM y */ -- SELECT z\nSELECT 1") == [(WORD, "SELECT"), (NUMBER, "1")]


def test_an_unclosed_block_comment_runs_to_the_end():
    assert _kinds("a /* b c") == [(WORD, "a")]


def test_statements_are_split_on_semicolons_and_empty_ones_dropped():
    found = list(statements("SELECT 1;; SELECT a, b;"))

    assert [[t.text for t in s.tokens] for s in found] == [["SELECT", "1"], ["SELECT", "a", ",", "b"]]
    assert not any(s.too_large for s in found)


def test_a_semicolon_inside_a_string_or_comment_does_not_split():
    assert len(list(statements("SELECT ';' -- ;\nFROM t /* ; */"))) == 1


def test_a_statement_over_the_limit_is_too_large_and_keeps_only_the_first_tokens():
    (statement,) = statements("a b c d e", limit=2)

    assert statement.too_large is True
    assert [t.text for t in statement.tokens] == ["a", "b"]


def test_a_too_large_statement_does_not_affect_the_next_one():
    found = list(statements("a b c d; e", limit=2))

    assert [(len(s.tokens), s.too_large) for s in found] == [(2, True), (1, False)]


def test_many_unclosed_templates_tokenize_in_linear_time():
    text = "SELECT " + "{{ a " * 200_000
    started = time.perf_counter()

    count = sum(1 for _ in tokens(text))

    assert count > 0
    assert time.perf_counter() - started < 5


def test_many_unclosed_comments_tokenize_in_linear_time():
    text = "SELECT a " + "/* " * 200_000
    started = time.perf_counter()

    assert [t.text for t in tokens(text)] == ["SELECT", "a"]
    assert time.perf_counter() - started < 5


def test_many_unclosed_double_quotes_tokenize_in_linear_time():
    text = 'SELECT "a\n' * 100_000
    started = time.perf_counter()

    count = sum(1 for _ in tokens(text))

    assert count == 300_000
    assert time.perf_counter() - started < 5
