"""SQL and Jinja scanning is a single linear pass, and matches the regexes it replaced."""
import itertools
import re
import time

import pytest

from arche_lib.langs.sql_tables import _jinja_spans, _placeholders, tables

# The patterns the linear scan replaced; they are the reference for well-formed and malformed input.
OLD_JINJA_SPAN = re.compile(r"\{#.*?#\}|\{\{.*?\}\}|\{%.*?%\}", re.S)
OLD_PLACEHOLDER = re.compile(r"\{\{.*?\}\}|\$\{[^}\n]*\}|\$[A-Za-z_]\w*", re.S)

LIMIT_SECONDS = 2.0
CORPUS = [
    "", "{", "{{", "}}", "{{}}", "{{}", "{{{}}", "{{ a }} b {{ c }}", "{# a #} {{ b }} {% c %}",
    "{{ {# }} #}", "{# {{ #} }}", "{% {{ %} }}", "{{ {% }} %}", "{#}", "{#}#}", "{%}%}",
    "{{ a\n}}\nb {{ c", "{# x", "x #}", "{{ unclosed {# closed #} {% also %}", "{# open {{ closed }} {% x %}",
    "select * from {{ ref('a') }} join {# c #} b {% if x %}c{% endif %}",
    "a ${x} b ${ y\n} c $z d $1 e $ f ${", "${a${b}", "$${a}", "${}", "$_x9 $é", "${{a}}", "{{${a}}}",
    "${ {{ } }}", "{{ ${ }} }", "x{{a}}y${b}z$c", "{{ a }} {{ b", "{{ a }}\n{{\nb\n}}\n${c\n}", "{{{{ a }} }}",
    "{% a {# b %} #}", "{# a {% b #} %}", "{{ a {% b }} %}",
]


def _old_placeholders(source):
    return OLD_PLACEHOLDER.sub(lambda match: "{}" + "\n" * match.group().count("\n"), source)


@pytest.mark.parametrize("source", CORPUS)
def test_the_span_scan_matches_the_old_regex(source):
    assert _jinja_spans(source) == [(m.start(), m.end()) for m in OLD_JINJA_SPAN.finditer(source)]


@pytest.mark.parametrize("source", CORPUS)
def test_the_placeholder_scan_matches_the_old_regex(source):
    assert _placeholders(source) == _old_placeholders(source)


def test_both_scans_match_the_old_regexes_over_every_short_string_of_the_openers():
    pieces = ["{{", "}}", "{%", "%}", "{#", "#}", "${", "}", "$a", "\n", "x", "{", "$"]
    for size in range(0, 5):
        for combo in itertools.product(pieces, repeat=size):
            source = "".join(combo)
            assert _jinja_spans(source) == [(m.start(), m.end()) for m in OLD_JINJA_SPAN.finditer(source)], source
            assert _placeholders(source) == _old_placeholders(source), source


@pytest.mark.parametrize("opener", ["{{ ", "{% ", "{# ", "${ ", "select ref {{ ", "select ref {% ", "select ref {# "])
def test_unclosed_openers_scan_in_linear_time(opener):
    source = opener * 100_000

    started = time.perf_counter()
    tables("q.sql", source)
    elapsed = time.perf_counter() - started

    assert elapsed < LIMIT_SECONDS, f"{opener!r}: {elapsed:.2f}s"


def test_many_refs_scan_in_linear_time():
    source = "".join(f"select * from {{{{ ref('m{index}') }}}}\n" for index in range(50_000))

    started = time.perf_counter()
    result = tables("q.sql", source)
    elapsed = time.perf_counter() - started

    assert len(result["dbt_refs"]) == 50_000 and result["dbt_refs"][-1]["line"] == 50_000
    assert elapsed < LIMIT_SECONDS, f"{elapsed:.2f}s"
