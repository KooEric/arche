import pytest

from arche_lib import lexer
from arche_lib.lexer import DART, JS, mask


def test_masking_keeps_length_and_newlines():
    source = "const a = 'x'; // note\n/* block\n   more */ const b = 2;\n"

    masked = mask(source, JS)

    assert len(masked.text) == len(source)
    lines = masked.text.split("\n")
    assert lines[0].rstrip() == "const a = ' ';"
    assert lines[1].strip() == ""
    assert lines[2].strip() == "const b = 2;"
    assert [len(line) for line in lines] == [len(line) for line in source.split("\n")]


def test_string_bodies_are_blanked_and_recorded_by_quote_offset():
    source = 'import x from "./mod";\n'

    masked = mask(source, JS)

    assert masked.text == 'import x from "     ";\n'
    assert masked.strings == {source.index('"'): "./mod"}


def test_comment_markers_inside_strings_are_not_comments():
    masked = mask("const url = 'http://x'; const y = 1;\n", JS)

    assert "const y = 1;" in masked.text


def test_quotes_and_braces_inside_comments_are_ignored():
    masked = mask("// it's { broken\nconst a = 1;\n/* ' { */ const b = 2;\n", JS)

    assert masked.balanced is True
    assert masked.strings == {}
    assert "const b = 2;" in masked.text


def test_escaped_quote_does_not_end_a_string():
    masked = mask(r"const s = 'it\'s'; const t = 1;" + "\n", JS)

    assert list(masked.strings.values()) == [r"it\'s"]
    assert "const t = 1;" in masked.text


def test_unterminated_quote_stops_at_the_end_of_the_line():
    masked = mask("const s = 'oops\nconst t = { a: 1 };\n", JS)

    assert "const t = { a: 1 };" in masked.text
    assert masked.balanced is True


def test_template_literal_is_blanked_but_its_interpolated_code_is_kept():
    source = "const s = `a ${fn({ k: 'v' })} b { not code`;\nconst t = 1;\n"

    masked = mask(source, JS)

    assert "fn({ k: ' ' })" in masked.text
    assert "not code" not in masked.text
    assert masked.balanced is True
    assert "const t = 1;" in masked.text


def test_nested_template_literals():
    masked = mask("const s = `a ${`b ${c} d`} e`;\nconst t = 1;\n", JS)

    assert masked.balanced is True
    assert "const t = 1;" in masked.text


@pytest.mark.parametrize(
    "source",
    [
        "const re = /['\"{]/g;\nconst t = 1;\n",
        "if (/[/'{]/.test(s)) { run(); }\nconst t = 1;\n",
        "return /\\/'{/.exec(s);\nconst t = 1;\n",
        "items.filter(x => /'{/.test(x));\nconst t = 1;\n",
    ],
)
def test_regex_literals_do_not_start_strings_or_blocks(source):
    masked = mask(source, JS)

    assert masked.strings == {}
    assert masked.balanced is True
    assert "const t = 1;" in masked.text


@pytest.mark.parametrize("source", ["const half = total / 2; const s = 'x';\n", "a = b / c / d; s = 'x';\n"])
def test_division_is_not_a_regex(source):
    masked = mask(source, JS)

    assert list(masked.strings.values()) == ["x"]


def test_jsx_apostrophe_after_a_word_is_text_not_a_string():
    source = "const A = () => <p>Don't stop {count}</p>;\nconst t = 'x';\n"

    masked = mask(source, JS)

    assert list(masked.strings.values()) == ["x"]
    assert masked.balanced is True


def test_jsx_closing_tag_slash_is_not_a_regex():
    source = "const A = () => <div><b>'q'</b></div>;\nconst t = 1;\n"

    masked = mask(source, JS)

    assert list(masked.strings.values()) == ["q"]
    assert "const t = 1;" in masked.text


def test_string_directly_after_a_keyword_is_still_a_string():
    masked = mask("export * from'./a';\n", JS)

    assert list(masked.strings.values()) == ["./a"]


def test_line_depths_and_depth_at_follow_all_bracket_kinds():
    source = "a(\n  [\n    { x: 1 }\n  ]\n)\nb\n"

    masked = mask(source, JS)

    assert masked.line_depths == (0, 1, 2, 2, 1, 0, 0)
    assert lexer.depth_at(masked, source.index("x")) == 3
    assert lexer.line_of(masked, source.index("x")) == 3
    assert lexer.line_of(masked, 0) == 1


@pytest.mark.parametrize("source", ["function f() {\n", "}\nconst a = 1;\n", "a(()\n"])
def test_unbalanced_brackets_are_reported(source):
    assert mask(source, JS).balanced is False


def test_dart_triple_quoted_string_spans_lines():
    source = "const s = '''line one\n it's { two\n''';\nconst t = 1;\n"

    masked = mask(source, DART)

    assert masked.balanced is True
    assert "const t = 1;" in masked.text
    assert list(masked.strings.values()) == ["line one\n it's { two\n"]


def test_dart_interpolation_keeps_code_and_handles_nested_quotes():
    source = "final s = 'total: ${map['key']} { not code';\nfinal t = 1;\n"

    masked = mask(source, DART)

    assert "map['   ']" in masked.text
    assert "not code" not in masked.text
    assert masked.balanced is True
    assert "final t = 1;" in masked.text


def test_dart_raw_string_ignores_escapes_and_interpolation():
    source = "final p = r'C:\\dir\\${x}\\'; final t = 1;\n"

    masked = mask(source, DART)

    assert list(masked.strings.values()) == [r"C:\dir\${x}" + "\\"]
    assert "final t = 1;" in masked.text


def test_dart_block_comments_nest():
    masked = mask("/* a /* b */ still comment { */ final t = 1;\n", DART)

    assert masked.balanced is True
    assert masked.text.strip() == "final t = 1;"


def test_dart_has_no_regex_or_template_literals():
    masked = mask("final a = b / c / d; final s = `x`; final t = 'y';\n", DART)

    assert list(masked.strings.values()) == ["y"]
