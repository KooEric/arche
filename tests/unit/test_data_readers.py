"""CSV, TSV, JSON and JSONL rows."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/arche/scripts"))
from arche_lib.data import readers
from arche_lib.data.readers import ReadStats, fix_header, flatten, rows
from arche_lib.errors import ParseError


def _read(tmp_path, name, content, fmt):
    path = tmp_path / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    stats = ReadStats()
    return list(rows(path, fmt, stats)), stats


def test_csv_delimiter_is_sniffed_and_bom_is_dropped(tmp_path):
    found, _ = _read(tmp_path, "a.csv", "﻿id;name\n1;Kim\n2;Lee\n", "csv")

    assert found == [{"id": "1", "name": "Kim"}, {"id": "2", "name": "Lee"}]


def test_tsv_is_always_tab_and_short_rows_are_counted(tmp_path):
    found, stats = _read(tmp_path, "a.tsv", "a\tb\n1\t2\n3\n", "tsv")

    assert found == [{"a": "1", "b": "2"}, {"a": "3"}]
    assert stats.malformed_rows == 1


def test_blank_and_repeated_header_names_are_fixed():
    assert fix_header(["id", "", "id", " "]) == (["id", "column_2", "id_2", "column_4"],
                                                 {"column_2": "", "id_2": "id", "column_4": " "})


def test_undecodable_bytes_are_counted(tmp_path):
    _, stats = _read(tmp_path, "a.csv", b"x\n\xff\n", "csv")

    assert stats.encoding_errors == 1


def test_an_oversized_field_fails_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(readers, "FIELD_LIMIT", 10)
    with pytest.raises(ParseError, match="field_too_large"):
        _read(tmp_path, "a.csv", "x\n" + "y" * 20 + "\n", "csv")


def test_json_takes_an_array_of_objects_or_one_object(tmp_path):
    assert _read(tmp_path, "a.json", '[{"a": 1}, {"a": 2}]', "json")[0] == [{"a": 1}, {"a": 2}]
    assert _read(tmp_path, "b.json", '{"a": 1}', "json")[0] == [{"a": 1}]
    with pytest.raises(ParseError, match="not_records"):
        _read(tmp_path, "c.json", "[1, 2]", "json")
    with pytest.raises(ParseError, match="json_error"):
        _read(tmp_path, "d.json", "{", "json")


def test_large_json_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(readers, "JSON_MAX_BYTES", 4)
    with pytest.raises(ParseError, match="json_too_large"):
        _read(tmp_path, "a.json", '[{"a": 1}]', "json")


def test_jsonl_skips_blank_lines_and_counts_bad_ones(tmp_path):
    found, stats = _read(tmp_path, "a.jsonl", '{"a": 1}\n\nnope\n[1]\n{"a": 2}\n', "jsonl")

    assert found == [{"a": 1}, {"a": 2}]
    assert stats.malformed_rows == 2


def test_nested_objects_flatten_to_three_levels():
    assert flatten({"a": {"b": {"c": {"d": 1}}, "x": [1]}, "y": 2}) == {"a.b.c": {"d": 1}, "a.x": [1], "y": 2}


def test_different_headers_in_one_dataset_are_drift():
    stats = ReadStats()
    stats.note_header(["a", "b"], {})
    stats.note_header(["a", "c"], {})

    assert stats.schema_drift is True


def test_a_jsonl_line_over_the_limit_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(readers, "JSONL_LINE_LIMIT", 20)
    path = tmp_path / "a.jsonl"
    path.write_text('{"a": 1}\n{"a": "' + "x" * 40 + '"}\n', encoding="utf-8")

    with pytest.raises(ParseError, match="line_too_large"):
        list(rows(path, "jsonl", ReadStats()))
