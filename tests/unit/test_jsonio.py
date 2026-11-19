import pytest

from arche_lib import jsonio
from arche_lib.errors import IntegrityError


def test_dumps_sorts_keys_keeps_unicode_and_ends_with_newline():
    assert jsonio.dumps({"b": 1, "a": "한글"}) == '{\n  "a": "한글",\n  "b": 1\n}\n'


def test_write_then_read_round_trips_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "nested" / "data.json"

    jsonio.write_json(target, {"k": [1, 2]})

    assert jsonio.read_json(target) == {"k": [1, 2]}
    assert [path.name for path in target.parent.iterdir()] == ["data.json"]


def test_write_replaces_existing_content(tmp_path):
    target = tmp_path / "data.json"
    jsonio.write_json(target, {"v": 1})

    jsonio.write_json(target, {"v": 2})

    assert jsonio.read_json(target) == {"v": 2}


def test_read_missing_file_raises_integrity_error(tmp_path):
    with pytest.raises(IntegrityError, match="missing input file"):
        jsonio.read_json(tmp_path / "absent.json")


def test_read_invalid_json_raises_integrity_error(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    with pytest.raises(IntegrityError, match="invalid JSON"):
        jsonio.read_json(broken)
