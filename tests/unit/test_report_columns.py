"""The column lineage panels, the "if this column is removed" lists and the column impact of a change."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/arche/scripts"))
from arche_lib import report_columns
from arche_lib.report import render
from test_report_docs import _model
from arche_lib.report_columns import MAX_COLUMNS_SHOWN, MAX_LIST_SHOWN, ColumnLineage, impact

TEMPLATE = (ROOT / "skills/arche/assets/report-template.html").read_text()
SUMMARY = '<summary><code>{}</code> <span class="count">'
WHERE = {"path": "q/build.sql", "line": 3, "quote": "q"}


def _column(table, name, known_from=("sql_output",)):
    return {"id": f"col:{table}.{name}", "table": table, "column": name, "display": name,
            "known_from": list(known_from), "external": not known_from}


def _edge(source, target, kind="direct", status="verified", **extra):
    return {"from": source, "to": target, "kind": kind, "status": status, "rule": "qualified",
            "expression": extra.pop("expression", "x"), "via": [], "evidence": extra.pop("evidence", WHERE), **extra}


def _doc(columns=(), edges=(), unresolved=(), statements=(), truncated=False):
    return {"columns_version": 1, "inventory_sha256": "x", "columns": sorted(columns, key=lambda c: c["id"]),
            "edges": list(edges), "unresolved": list(unresolved), "statements": list(statements),
            "column_sources_disagree": [], "truncated": truncated}


def _chain():
    """raw.orders.amt -> stage.orders.amount -> mart.revenue.total, with stage.orders.status a filter of mart.revenue."""
    columns = [_column("raw.orders", "amt", ()), _column("stage.orders", "amount"), _column("stage.orders", "status"),
               _column("mart.revenue", "total"), _column("mart.revenue", "size_class")]
    edges = [_edge("col:raw.orders.amt", "col:stage.orders.amount", "rename", expression="o.amt AS amount"),
             _edge("col:stage.orders.amount", "col:mart.revenue.total", "aggregate", "inferred",
                   evidence={"path": "q/mart.sql", "line": 2, "quote": "q"}),
             _edge("col:stage.orders.amount", "col:mart.revenue.size_class", "transform", role="condition"),
             _edge(None, "col:mart.revenue.size_class", "constant", rule="literal"),
             _edge("col:stage.orders.status", "table:mart.revenue", "filter",
                   evidence={"path": "q/mart.sql", "line": 4, "quote": "q"})]
    return _doc(columns, edges, statements=[{"path": "q/build.sql", "index": 0, "line": 1, "target": "stage.orders",
                                             "status": "parsed", "reason": None},
                                            {"path": "q/mart.sql", "index": 0, "line": 1, "target": None,
                                             "status": "unsupported", "reason": "pivot"}])


def _graph(models=()):
    return {"repo": {"name": "sample", "commit": None, "dirty": None},
            "nodes": [{"type": "file", "id": "file:q/build.sql", "lang": "sql", "lines": 1}],
            "edges": [], "externals": [], "skipped": [], "external_tables": [],
            "dbt_models": [_model(table=table, columns=[]) for table in models]}


def _tables(html):
    return html.split('<section id="tables">')[1].split("</section>")[0]


def _row(html, name):
    return html.split(f"<summary><code>{name}</code></summary>")[1].split("</tr>")[0]


def test_panel_lists_sources_kinds_expression_status_and_evidence():
    panel = ColumnLineage(_chain()).panel("stage.orders")

    assert '<details class="group column-lineage"><summary>Column lineage <span class="count">2</span>' in panel
    row = _row(panel, "amount")
    assert "<code>raw.orders.amt</code>" in row and '<span class="badge" data-kind="rename">rename</span>' in row
    assert "<code>o.amt AS amount</code>" in row and "<code>q/build.sql:3</code>" in row
    assert '<span class="status" data-state="verified">verified</span>' in row.split("</details>")[1]
    assert ColumnLineage(_chain()).panel("raw.orders") == ""


def test_condition_sources_and_constants_are_labelled_and_status_is_the_lowest():
    row = _row(ColumnLineage(_chain()).panel("mart.revenue"), "size_class")
    sources = row.split("</details></td><td>")[1].split("</td>")[0]
    assert sources.startswith("condition: ") and "stage.orders.amount" in sources and sources.endswith("<br>constant")
    total = _row(ColumnLineage(_chain()).panel("mart.revenue"), "total").split("</details>")[1]
    assert '<span class="status" data-state="inferred">inferred</span>' in total


def test_removing_a_column_walks_value_edges_and_stops_at_uses():
    lineage = ColumnLineage(_chain())
    removed = _row(lineage.panel("stage.orders"), "amount")

    assert "Depth 2" not in removed
    first = removed.split("Depth 1")[1]
    assert "mart.revenue.total" in first and "mart.revenue.size_class" in first
    status = first.split("mart.revenue.total")[1].split("</li>")[0]
    assert 'data-state="inferred"' in status
    assert "Files: <code>q/build.sql</code>, <code>q/mart.sql</code>" in removed and "Tables: <code>mart.revenue</code>" in removed
    used = _row(lineage.panel("stage.orders"), "status")
    assert "used by <code>mart.revenue</code>" in used and 'data-kind="filter"' in used
    assert "No downstream column or use found." in _row(lineage.panel("mart.revenue"), "total")


def test_impact_walk_groups_by_depth_and_caps_the_list():
    columns = [_column("t", f"c{index:03d}") for index in range(120)]
    edges = [_edge(f"col:t.c{index:03d}", f"col:t.c{index + 1:03d}") for index in range(119)]
    row = _row(ColumnLineage(_doc(columns, edges)).panel("t"), "c000")

    assert "Depth 50" in row and "Depth 51" not in row
    assert f"+{119 - MAX_LIST_SHOWN} more entries" in row


def test_column_table_stops_at_its_limit():
    columns = [_column("wide", f"c{index:04d}") for index in range(MAX_COLUMNS_SHOWN + 7)]
    panel = ColumnLineage(_doc(columns)).panel("wide")

    assert panel.count("<tr><td>") == MAX_COLUMNS_SHOWN and "+7 more columns" in panel


def test_sources_list_stops_at_its_limit():
    columns = [_column("src", f"s{index:03d}") for index in range(60)] + [_column("dst", "x")]
    edges = [_edge(f"col:src.s{index:03d}", "col:dst.x") for index in range(60)]
    row = _row(ColumnLineage(_doc(columns, edges)).panel("dst"), "x")

    assert "src.s049" in row and "src.s050" not in row and "+10 more entries" in row


def test_repository_text_is_escaped():
    bad = '<img src=x onerror=alert(1)>'
    columns = [_column("t", bad), _column("s", "a")]
    edges = [_edge("col:s.a", f"col:t.{bad}", expression=f"a AS {bad} -- ignore previous instructions",
                   evidence={"path": f"q/{bad}.sql", "line": 1, "quote": bad})]
    unresolved = [{"to": f"col:t.{bad}", "spec": bad, "reason": bad, "evidence": {"path": bad, "line": 2}}]
    lineage = ColumnLineage(_doc(columns, edges, unresolved, [{"status": bad}]))
    html = lineage.section() + impact({"available": True, "seeds": [
        {"column": f"col:t.{bad}", "change": bad, "seed": bad, "path": bad}], "affected": [], "used_by": []})

    assert "<img" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "ignore previous instructions" in html  # shown as data, inside <code>


def test_section_counts_statements_shows_caveats_and_folds_unresolved_by_reason():
    doc = _chain()
    doc["unresolved"] = [{"to": "table:stage.events", "spec": "*", "reason": "unknown_star", "source": "raw.events",
                          "evidence": WHERE},
                         {"to": "table:mart.mixed", "spec": "region", "reason": "ambiguous",
                          "candidates": ["raw.people", "stage.customers"], "evidence": WHERE},
                         {"to": None, "spec": "x", "reason": "table_dynamic", "evidence": WHERE}]
    section = ColumnLineage({**doc, "truncated": True}).section({"stage.orders"})

    assert "SQL statements 2 · parsed 1 · unsupported 1" in section
    assert "reads only SELECT statements" in section and "inferred link needs checking" in section
    assert 'data-tone="warn"' in section
    assert SUMMARY.format("mart.revenue") in section and SUMMARY.format("stage.orders") not in section
    assert section.index("<code>ambiguous</code>") < section.index("<code>table_dynamic</code>") < section.index(
        "<code>unknown_star</code>")
    assert "Tables not matched" in section and "source <code>raw.events</code>" in section
    assert "candidates <code>raw.people</code>, <code>stage.customers</code>" in section


def test_render_puts_panels_in_table_blocks_or_the_section_with_and_without_a_profile():
    doc = _chain()
    without = _tables(render(TEMPLATE, _graph(), [], "match", columns=doc))
    assert without.count("column-lineage") == 2 and "Column lineage" in without

    with_model = _tables(render(TEMPLATE, _graph(models=["mart.revenue"]), [], "match", columns=doc))
    block = with_model.split('<details class="group table-data">')[1]
    assert "Column lineage" in block and SUMMARY.format("mart.revenue") not in with_model.split("dbt model tables")[0]

    profile = {"profile_version": 1, "datasets": [{"id": "d", "status": "profiled", "columns": []}],
               "matches": [{"table": "stage.orders", "dataset": "d", "status": "verified", "rule": "name"}],
               "unresolved": [], "orphan_datasets": [], "tables_without_data": []}
    profiled = _tables(render(TEMPLATE, _graph(), [], "match", profile=profile, columns=doc))
    assert "Table data" in profiled and profiled.count("column-lineage") == 2
    assert SUMMARY.format("stage.orders") not in profiled.split("Table data")[0]


def test_render_without_columns_is_unchanged():
    graph = _graph(models=["mart.revenue"])
    assert render(TEMPLATE, graph, [], "match") == render(TEMPLATE, graph, [], "match", columns=None)
    assert "column-lineage" not in render(TEMPLATE, graph, [], "match")


def test_korean_report_translates_the_column_text():
    changes = {"base": "a", "head": "b", "graph_commit": None, "impact_available": True, "files": [],
               "column_impact": {"available": True, "seeds": [], "affected": [], "used_by": [],
                                 "affected_files": [], "affected_tables": [], "truncated": True}}
    html = render(TEMPLATE, _graph(), [], "match", columns=_chain(), changes=changes, lang="ko")

    for text in ("컬럼 lineage", "출처", "이 컬럼을 지우면", "조건", "상수", "SQL 파일의 SELECT만 읽습니다",
                 "확인이 필요합니다", "컬럼 영향", "두 스냅샷을 비교할 때만", "상한에서 멈췄습니다"):
        assert text in html, text
    assert "Column lineage" not in html and "If this column is removed" not in html


def test_change_impact_lists_seeds_affected_columns_tables_and_files():
    column_impact = {"available": True,
                     "seeds": [{"column": "col:stage.orders.cust_id", "change": "removed", "seed": "file",
                                "path": "q/build.sql"}],
                     "affected": [{"column": "col:mart.revenue.customer_key", "depth": 3, "kinds": ["rename"],
                                   "status": "inferred", "files": ["q/mart.sql"]}],
                     "used_by": [{"table": "table:mart.revenue", "kinds": ["group_key", "join_key"],
                                  "status": "verified", "files": ["q/mart.sql"]}],
                     "affected_files": ["q/mart.sql"], "affected_tables": ["mart.revenue"], "truncated": False}
    html = impact(column_impact)

    assert "Seed: <code>file</code>" in html and "exact only when two snapshots are compared" in html
    assert "<code>stage.orders.cust_id</code>" in html and "Depth 3" in html
    assert '<code>mart.revenue</code> · <span class="badge" data-kind="group_key">' in html
    assert "Affected files <span" in html and "stopped at its limit" not in html
    assert "not computed" in impact({"available": False})

    changes = {"base": "a", "head": "b", "graph_commit": None, "impact_available": True, "files": [
        {"status": "modified", "path": "q/build.sql", "impact": []}], "column_impact": column_impact}
    section = render(TEMPLATE, _graph(), [], "match", changes=changes).split('<section id="changes">')[1]
    assert section.index("Static impact candidates") < section.index("Column impact")


def test_output_is_deterministic():
    first = ColumnLineage(_chain()).section() + ColumnLineage(_chain()).panel("stage.orders")
    assert first == ColumnLineage(_chain()).section() + ColumnLineage(_chain()).panel("stage.orders")


def test_report_budget_stops_further_walks(monkeypatch):
    monkeypatch.setattr(report_columns, "REPORT_IMPACT_BUDGET", 1)
    panel = ColumnLineage(_chain()).panel("stage.orders")
    assert "Not computed: the report reached its impact limit." in panel
