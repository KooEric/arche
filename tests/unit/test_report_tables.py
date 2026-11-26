"""The report section for external tables and table names that were not followed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/arche/scripts"))
from arche_lib.report import render

TEMPLATE = (Path(__file__).resolve().parents[2] / "skills/arche/assets/report-template.html").read_text()
FILES = ("q/load.sql", "q/report.sql")


def _unresolved(line, table, reason):
    return {"kind": "READS", "from": "file:q/report.sql", "to": None, "origin": "script", "status": "unresolved",
            "table": table, "spec": table, "reason": reason, "evidence": {"path": "q/report.sql", "line": line, "quote": "q"}}


def _graph(edges=(), external=(), files=FILES):
    return {
        "repo": {"name": "sample", "commit": None, "dirty": None},
        "nodes": [{"type": "file", "id": "file:" + path, "lang": "sql", "lines": 1} for path in files],
        "edges": list(edges), "externals": [], "skipped": [],
        "external_tables": [{"from": "file:" + path, "table": table, "line": line} for path, table, line in external],
    }


def _section(html):
    return html.split('<section id="tables">')[1].split("</section>")[0]


def test_external_tables_are_folded_by_schema_with_their_readers():
    external = [("q/load.sql", "staging.orders", 1), ("q/report.sql", "staging.orders", 4),
                ("q/report.sql", "staging.users", 5), ("q/report.sql", "calendar", 6)]
    section = _section(render(TEMPLATE, _graph(external=external), [], "match"))

    assert "External tables" in section
    assert '<code>staging</code> <span class="count">2</span>' in section
    assert section.index("<code>staging</code>") < section.index("no schema")
    assert "<code>staging.orders</code>" in section
    assert '<code>q/load.sql</code></a>:1' in section and '<code>q/report.sql</code></a>:4' in section
    assert "<code>q/load.sql:1</code>" not in section


def test_names_that_were_not_followed_are_folded_by_reason():
    edges = [_unresolved(2, "a.daily_{}", "dynamic"), _unresolved(3, "{}.ledger", "ambiguous")]
    section = _section(render(TEMPLATE, _graph(edges=edges), [], "match"))

    assert "<code>dynamic</code> <span" in section and "<code>ambiguous</code> <span" in section
    assert "<code>a.daily_{}</code> · <code>q/report.sql:2</code>" in section


def test_names_from_the_repository_are_escaped():
    section = _section(render(TEMPLATE, _graph(external=[("q/load.sql", "<b>.<i>", 1)]), [], "match"))

    assert "<b>" not in section and "<i>" not in section
    assert "&lt;b&gt;.&lt;i&gt;" in section


def test_a_repository_without_tables_has_no_section_or_navigation_entry():
    html = render(TEMPLATE, _graph(), [], "match")

    assert 'id="tables"' not in html and 'href="#tables"' not in html


def test_a_graph_written_before_this_feature_still_renders():
    graph = _graph()
    del graph["external_tables"]

    assert 'id="tables"' not in render(TEMPLATE, graph, [], "match")


def test_the_section_is_translated():
    html = render(TEMPLATE, _graph(external=[("q/load.sql", "staging.orders", 1)]), [], "match", lang="ko")

    assert "외부 테이블" in _section(html)


def test_external_table_readers_are_capped_with_a_note_for_the_rest():
    external = [(f"q/r{n}.sql", "staging.orders", n) for n in range(1, 8)]
    section = _section(render(TEMPLATE, _graph(external=external, files=[e[0] for e in external]), [], "match"))

    assert section.count("</code></a>:") == 5
    assert "+2 more" in section
    assert "</code></a>:6" not in section


def test_the_reader_note_is_translated():
    external = [(f"q/r{n}.sql", "staging.orders", n) for n in range(1, 8)]
    section = _section(render(TEMPLATE, _graph(external=external, files=[e[0] for e in external]), [], "match", lang="ko"))

    assert "외 2곳" in section


def test_large_reports_carry_the_table_name_in_file_details_as_escaped_json():
    edge = {"kind": "READS", "from": "file:q/report.sql", "to": "file:q/load.sql", "origin": "script",
            "status": "verified", "rule": "name", "table": "billing.<orders>",
            "evidence": {"path": "q/report.sql", "line": 2, "quote": "q"}}
    html = render(TEMPLATE, _graph(edges=[edge]), [], "match", lazy_file_limit=0)
    data = html.split('id="file-details">')[1].split("</script>")[0]

    assert '"billing.\\u003corders>"' in data
    assert "billing.<orders>" not in html


def test_an_empty_profile_shows_the_dbt_model_tables_like_no_profile():
    from arche_lib.report_data import table_data
    from arche_lib.i18n import translator

    graph = {"edges": [], "dbt_models": [{"model": "orders", "table": "{}.orders", "dynamic": False, "file": "file:m/orders.sql",
                                          "description": None, "doc": None, "tests": [], "columns": [],
                                          "defined_in": None, "duplicates": []}]}

    assert table_data(graph, {}, lambda node: node, translator("en")) == table_data(graph, None, lambda node: node, translator("en"))
