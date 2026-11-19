from arche_lib import jsonc
from arche_lib.langs import pathutil


def test_loads_plain_json():
    assert jsonc.loads('{"a": [1, 2]}') == {"a": [1, 2]}


def test_loads_strips_line_and_block_comments():
    text = '{\n  // line\n  "a": 1, /* block */ "b": 2\n}\n'

    assert jsonc.loads(text) == {"a": 1, "b": 2}


def test_loads_accepts_trailing_commas_even_before_a_comment():
    text = '{"paths": {"@/*": ["./src/*",],}, // done\n}'

    assert jsonc.loads(text) == {"paths": {"@/*": ["./src/*"]}}


def test_comment_markers_and_commas_inside_strings_are_kept():
    text = '{"url": "http://x/*y*/", "glob": "a,}", "q": "say \\"//hi\\""}'

    assert jsonc.loads(text) == {"url": "http://x/*y*/", "glob": "a,}", "q": 'say "//hi"'}


def test_loads_ignores_a_byte_order_mark():
    assert jsonc.loads('\ufeff{"a": 1}') == {"a": 1}


def test_loads_returns_none_for_missing_or_broken_text():
    assert jsonc.loads(None) is None
    assert jsonc.loads("{not json") is None


def test_parent_of_a_top_level_file_is_the_root():
    assert pathutil.parent("a.ts") == ""
    assert pathutil.parent("src/lib/a.ts") == "src/lib"


def test_join_norm_resolves_dots_and_rejects_escapes():
    assert pathutil.join_norm("src/lib", "../util/x") == "src/util/x"
    assert pathutil.join_norm("src", "./a") == "src/a"
    assert pathutil.join_norm("", "./a") == "a"
    assert pathutil.join_norm("src", "..") == ""
    assert pathutil.join_norm("src", "../../x") is None
    assert pathutil.join_norm("src", "/etc/passwd") is None


def test_source_line_is_stripped_and_bounded():
    lines = ["  import a from 'a'  ", "x" * 300]

    assert pathutil.source_line(lines, 1) == "import a from 'a'"
    assert len(pathutil.source_line(lines, 2)) == pathutil.MAX_QUOTE_CHARS
    assert pathutil.source_line(lines, 3) == ""
