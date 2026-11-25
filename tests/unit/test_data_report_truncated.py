"""The report says when discovery stopped at a limit."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/arche/scripts"))
from arche_lib.i18n import translator
from arche_lib.report_data import table_data

NOTE = ("Discovery stopped at the file or dataset limit; tables listed without data may have data that was not read.")


def _html(truncated, lang="en"):
    profile = {"datasets": [], "matches": [], "tables_without_data": ["a.b"], "orphan_datasets": [], "unresolved": []}
    if truncated is not None:
        profile["truncated"] = truncated
    return table_data({"edges": []}, profile, lambda node: node, translator(lang))


def test_a_truncated_discovery_is_called_out():
    assert NOTE in _html(True)
    assert "파일·데이터셋 상한에서 찾기를 멈췄습니다. 데이터 없는 테이블로 나온 것도 읽지 못한 데이터가 있을 수 있습니다." in _html(True, "ko")


def test_a_complete_discovery_has_no_such_note():
    assert NOTE not in _html(False) and NOTE not in _html(None)
