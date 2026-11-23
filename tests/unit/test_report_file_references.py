"""The report section for SQL files and the paths that name them."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/arche/scripts"))
from arche_lib.report import render

TEMPLATE = (Path(__file__).resolve().parents[2] / "skills/arche/assets/report-template.html").read_text()


def _edge(line, to=None, spec=None, reason=None):
    edge = {"kind": "REFERENCES", "from": "file:dags/dag.py", "to": to, "origin": "script",
            "status": "verified" if to else "unresolved",
            "evidence": {"path": "dags/dag.py", "line": line, "quote": "q"}}
    return edge if to else {**edge, "spec": spec, "reason": reason}


def _graph(edges, sql=("dags/sql/load.sql", "dags/sql/unused.sql")):
    return {
        "repo": {"name": "sample", "commit": None, "dirty": None},
        "nodes": [{"type": "file", "id": "file:dags/dag.py", "lang": "python", "lines": 3}]
                 + [{"type": "file", "id": "file:" + path, "lang": "sql", "lines": 1} for path in sql],
        "edges": edges, "externals": [], "skipped": [],
    }


def _section(html):
    return html.split('<section id="file-references">')[1].split("</section>")[0]


def test_sql_files_no_parsed_file_names_are_listed():
    section = _section(render(TEMPLATE, _graph([_edge(1, to="file:dags/sql/load.sql")]), [], "match"))

    assert "SQL files with no static reference" in section
    assert "<code>dags/sql/unused.sql</code>" in section
    assert "<code>dags/sql/load.sql</code>" not in section
    assert "run time" not in section


def test_dynamic_paths_warn_that_a_listed_file_may_still_be_used():
    edges = [_edge(1, to="file:dags/sql/load.sql"), _edge(2, spec="sql/{}.sql", reason="dynamic")]
    section = _section(render(TEMPLATE, _graph(edges), [], "match"))

    assert 'data-tone="warn"' in section and "1 path" in section
    assert "<code>sql/{}.sql</code> · <code>dags/dag.py:2</code>" in section


def test_unresolved_paths_are_folded_by_reason():
    edges = [_edge(3, spec="sql/gone.sql", reason="not_found"), _edge(4, spec="x.sql", reason="ambiguous")]
    section = _section(render(TEMPLATE, _graph(edges), [], "match"))

    assert "<code>not_found</code> <span" in section and "<code>ambiguous</code> <span" in section
    assert "<code>sql/gone.sql</code> · <code>dags/dag.py:3</code>" in section


def test_a_repository_without_sql_has_no_section_or_navigation_entry():
    html = render(TEMPLATE, _graph([], sql=()), [], "match")

    assert 'id="file-references"' not in html and 'href="#file-references"' not in html


def test_the_section_is_translated():
    html = render(TEMPLATE, _graph([_edge(1, to="file:dags/sql/load.sql")]), [], "match", lang="ko")

    assert "정적 참조가 없는 SQL 파일" in _section(html)


def test_unreferenced_files_are_folded_by_folder():
    sql = ("a/sql/one.sql", "a/sql/two.sql", "b/three.sql")
    section = _section(render(TEMPLATE, _graph([], sql=sql), [], "match"))

    assert section.index("<code>a/sql/</code>") < section.index("<code>b/</code>")
    assert '<code>a/sql/</code> <span class="count">2</span>' in section
    assert "<code>a/sql/one.sql</code>" in section


def test_paths_from_the_repository_are_escaped():
    edges = [_edge(2, spec="<b>&.sql", reason="not_found")]
    section = _section(render(TEMPLATE, _graph(edges, sql=("q/<i>.sql",)), [], "match"))

    assert "<b>" not in section and "<i>" not in section
    assert "&lt;b&gt;&amp;.sql" in section


def test_several_dynamic_paths_are_counted_in_korean_without_a_stray_space():
    edges = [_edge(2, spec="a/{}.sql", reason="dynamic"), _edge(3, spec="b/{}.sql", reason="dynamic")]
    html = render(TEMPLATE, _graph(edges), [], "match", lang="ko")

    assert "경로 2개가 실행 시점에 만들어집니다." in _section(html)


def test_a_file_detail_names_the_table_a_read_goes_through():
    read = {"kind": "READS", "from": "file:dags/sql/load.sql", "to": "file:dags/sql/unused.sql", "origin": "script",
            "status": "verified", "rule": "name", "table": "billing.<orders>",
            "evidence": {"path": "dags/sql/load.sql", "line": 1, "quote": "q"}}
    html = render(TEMPLATE, _graph([read]), [], "match")

    assert "<code>billing.&lt;orders&gt;</code>" in html and "billing.<orders>" not in html
