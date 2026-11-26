"""dbt model docs in the report's Tables section."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/arche/scripts"))
from arche_lib.report import render
from arche_lib.report_docs import documented_type, first_sentence

TEMPLATE = (Path(__file__).resolve().parents[2] / "skills/arche/assets/report-template.html").read_text()
TABLE = "{}_marts.orders"


def _model(**changes):
    model = {
        "model": "orders", "kind": "models", "project": "jaffle", "file": "file:models/marts/orders.sql",
        "table": TABLE, "dynamic": False, "alias_from": "default", "schema_from": "project",
        "description": "One row per order. Placed or shipped.", "doc": None,
        "tests": [{"name": "dbt_utils.expression_is_true"}],
        "columns": [
            {"name": "order_id", "description": "The key", "data_type": "bigint",
             "tests": [{"name": "not_null"}, {"name": "unique"}]},
            {"name": "status", "description": None, "data_type": "varchar(20)",
             "tests": [{"name": "accepted_values", "values": ["placed", "shipped"], "values_count": 2}]},
            {"name": "gone", "description": None, "data_type": None, "tests": []},
        ],
        "defined_in": {"path": "models/marts/schema.yml", "line": 3}, "duplicates": [],
    }
    return {**model, **changes}


def _graph(model):
    return {
        "repo": {"name": "sample", "commit": None, "dirty": None},
        "nodes": [{"type": "file", "id": f"file:{path}", "lang": lang, "lines": 1}
                  for path, lang in (("models/marts/orders.sql", "sql"), ("models/marts/schema.yml", "yaml"))],
        "edges": [], "externals": [], "skipped": [], "external_tables": [],
        "written_tables": [{"table": TABLE, "from": "file:models/marts/orders.sql", "line": 1, "via": "dbt:model"}],
        "dbt_models": [model], "dbt_sources": [], "dbt_docs_unlinked": [],
    }


def _column(name, kind, **extra):
    return {"name": name, "type": kind, "null_ratio": 0.0, "distinct": 4, "distinct_capped": False, "length": [1, 2],
            "patterns": [], "candidate_key": False, "sensitive": False, **extra}


def _profile(columns):
    return {"datasets": [{"id": "d0:orders.csv", "status": "profiled", "rows_read": 4, "rows_total": 4, "columns": columns}],
            "matches": [{"table": TABLE, "dataset": "d0:orders.csv", "status": "inferred", "rule": "table_name"}],
            "unresolved": [], "orphan_datasets": [], "tables_without_data": []}


def _section(html):
    return html.split('<section id="tables">')[1].split("</section>")[0]


def test_a_model_table_without_data_shows_its_docs():
    section = _section(render(TEMPLATE, _graph(_model()), [], "match"))

    assert "dbt model tables" in section and "<code>{}_marts.orders</code>" in section
    assert ">dbt</span> <span class=\"label\">One row per order.</span>" in section
    assert 'Model file: <a href="#file-0"><code>models/marts/orders.sql</code></a>' in section
    assert 'Documented in: <a href="#file-1"><code>models/marts/schema.yml</code></a>:3' in section
    assert "<code>dbt_utils.expression_is_true</code>" in section and "The key" in section
    assert "Not in data" not in section and "inferred" in section


def test_docs_and_data_are_joined_by_name_and_differences_are_marked():
    columns = [_column("ORDER_ID", "integer"), _column("status", "integer"), _column("extra", "string")]
    section = _section(render(TEMPLATE, _graph(_model()), [], "match", profile=_profile(columns)))

    assert "Table data" in section and section.count("<code>order_id</code>") == 1
    assert "<code>gone</code> <span class=\"badge\" data-tone=\"warn\">Not in data</span>" in section
    assert "<code>extra</code>" in section and "Not in docs" in section
    assert section.count("Type differs") == 1
    assert "<td>varchar(20)</td><td>integer</td>" in section


def test_test_hints_count_what_the_sample_shows_without_values():
    columns = [_column("order_id", "integer", null_ratio=0.25, distinct=3),
               _column("status", "string", distinct=2, sensitive=True)]
    model = _model(columns=[{**_model()["columns"][0]},
                            {"name": "status", "description": None, "data_type": None, "tests": [{"name": "unique"}]}])
    section = _section(render(TEMPLATE, _graph(model), [], "match", profile=_profile(columns)))

    assert "In the sample: null 25.0%" in section and section.count("In the sample: duplicates") == 1
    assert "Test hints come from the sample read and are not test results." in section


def test_unique_is_not_hinted_when_distinct_was_capped_or_nulls_hide_the_count():
    columns = [_column("order_id", "integer", distinct=2, distinct_capped=True)]
    capped = _section(render(TEMPLATE, _graph(_model()), [], "match", profile=_profile(columns)))
    nulls = _section(render(TEMPLATE, _graph(_model()), [], "match",
                            profile=_profile([_column("order_id", "integer", distinct=2, null_ratio=0.5)])))

    assert "duplicates" not in capped and "duplicates" not in nulls and "null 50.0%" in nulls


def test_descriptions_and_test_arguments_are_escaped():
    evil = "<script>x</script>"
    model = _model(description=evil, columns=[{"name": evil, "description": evil, "data_type": evil, "tests": [
        {"name": "accepted_values", "values": [evil], "values_count": 1},
        {"name": "relationships", "to": {"ref": evil}, "field": evil}]}])
    section = _section(render(TEMPLATE, _graph(model), [], "match"))

    assert "<script>" not in section and "&lt;script&gt;" in section


def test_korean_report_translates_the_docs_labels():
    section = _section(render(TEMPLATE, _graph(_model()), [], "match", lang="ko",
                              profile=_profile([_column("order_id", "integer", null_ratio=0.5)])))

    for text in ("모델 파일", "문서 위치", "데이터에 없음", "표본에서 null 50.0%", "판정이 아닙니다", "문서 타입", "추정입니다"):
        assert text in section, text


def test_a_dynamic_model_and_a_graph_without_dbt_keys_draw_no_docs():
    dynamic = _graph(_model(table=None, dynamic=True))
    dynamic["written_tables"] = []
    plain = {key: value for key, value in _graph(_model()).items() if not key.startswith("dbt_")}
    plain["written_tables"] = []

    assert render(TEMPLATE, dynamic, [], "match") == render(TEMPLATE, plain, [], "match")


def test_first_sentence_and_type_mapping():
    assert first_sentence("One row per order. More.") == "One row per order."
    assert first_sentence("주문 한 건이 한 행이다.\n다음") == "주문 한 건이 한 행이다."
    assert len(first_sentence("x" * 500)) == 120
    assert first_sentence(None) == ""
    assert [documented_type(t) for t in ("VARCHAR(20)", "int64", "timestamp_tz", "jsonb", None)] == [
        "string", "integer", "timestamp", None, None]
