"""The report shows when column names were replaced or the column list was cut."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/arche/scripts"))
from arche_lib.data import MAX_COLUMNS
from arche_lib.i18n import translator
from arche_lib.report_data import table_data

SUSPECT_EN = "The first row or some keys looked like data, so columns were renamed column_N or key_N."
SUSPECT_KO = "첫 행이나 일부 키가 데이터처럼 보여 컬럼 이름을 column_N·key_N으로 바꿨습니다."


def _html(lang="en", **flags):
    dataset = {"id": "d0:t.csv", "status": "profiled", "rows_read": 1, "columns": [], **flags}
    match = {"table": "a.b", "dataset": "d0:t.csv", "status": "inferred", "rule": "path"}
    profile = {"datasets": [dataset], "matches": [match], "tables_without_data": [], "orphan_datasets": [], "unresolved": []}
    return table_data({"edges": []}, profile, lambda node: node, translator(lang))


def test_a_suspect_header_is_called_out_in_both_languages():
    assert SUSPECT_EN in _html(header_suspect=True)
    assert SUSPECT_KO in _html("ko", header_suspect=True)


def test_cut_columns_are_called_out_in_both_languages():
    assert f"Only the first {MAX_COLUMNS} columns were profiled." in _html(columns_truncated=True)
    assert f"앞쪽 컬럼 {MAX_COLUMNS}개만 프로파일했습니다." in _html("ko", columns_truncated=True)


def test_a_plain_dataset_has_neither_note():
    html = _html()
    assert SUSPECT_EN not in html and "columns were profiled" not in html
