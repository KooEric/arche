"""Table data in the report's Tables section."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/arche/scripts"))
from arche_lib.report import render

TEMPLATE = (Path(__file__).resolve().parents[2] / "skills/arche/assets/report-template.html").read_text()


def _graph():
    return {
        "repo": {"name": "sample", "commit": None, "dirty": None},
        "nodes": [{"type": "file", "id": f"file:q/{name}.sql", "lang": "sql", "lines": 1} for name in ("load", "mart")],
        "edges": [{"kind": "READS", "from": "file:q/mart.sql", "to": "file:q/load.sql", "origin": "script", "status": "verified",
                   "rule": "name", "table": "acc.ledger", "evidence": {"path": "q/mart.sql", "line": 1, "quote": "q"}}],
        "externals": [], "skipped": [], "external_tables": [],
        "written_tables": [{"table": "acc.ledger", "from": "file:q/load.sql", "line": 1}],
    }


def _profile(column):
    return {
        "datasets": [{"id": "d0:acc/ledger", "status": "profiled", "rows_read": 2, "rows_total": 2, "columns": [column]}],
        "matches": [{"table": "acc.ledger", "dataset": "d0:acc/ledger", "status": "inferred", "rule": "path"}],
        "unresolved": [{"dataset": "d0:events.csv", "reason": "ambiguous", "candidates": ["a.events", "b.events"]}],
        "orphan_datasets": ["d0:tmp.csv"], "tables_without_data": ["mart.revenue"],
    }


def _section(html):
    return html.split('<section id="tables">')[1].split("</section>")[0]


COLUMN = {"name": "id", "type": "integer", "null_ratio": 0.0, "distinct": 2, "distinct_capped": False, "min": "1", "max": "2",
          "length": [1, 1], "patterns": [], "candidate_key": True, "sensitive": False}


def test_a_matched_table_shows_its_files_and_columns():
    section = _section(render(TEMPLATE, _graph(), [], "match", profile=_profile(COLUMN)))

    assert "Table data" in section and "<code>acc.ledger</code>" in section
    assert 'Written by: <a href="#file-0"><code>q/load.sql</code></a>' in section
    assert 'Read by: <a href="#file-1"><code>q/mart.sql</code></a>' in section
    assert "<code>1</code> – <code>2</code>" in section and ">key<" in section
    assert "<code>mart.revenue</code>" in section and "<code>d0:tmp.csv</code>" in section
    assert "<code>a.events</code>" in section


def test_names_from_data_are_escaped():
    column = {**COLUMN, "name": "<b>x</b>", "patterns": [["<i>", 1]]}
    section = _section(render(TEMPLATE, _graph(), [], "match", profile=_profile(column)))

    assert "<b>x</b>" not in section and "&lt;b&gt;x&lt;/b&gt;" in section and "<i>" not in section


def test_without_a_profile_the_section_is_unchanged():
    assert "Table data" not in _section(render(TEMPLATE, _graph(), [], "match"))


def test_korean_report_translates_the_data_labels():
    section = _section(render(TEMPLATE, _graph(), [], "match", profile=_profile(COLUMN), lang="ko"))

    assert "테이블 데이터" in section and "쓰는 파일" in section
