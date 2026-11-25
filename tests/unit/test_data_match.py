"""Datasets meet the tables the SQL reads and writes."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/arche/scripts"))
from arche_lib.data.match import load_map, match, name_parts
from arche_lib.errors import UsageError

ROOTS = {"d0": "landing"}


def _ds(path, kind="file", root="d0"):
    return {"id": f"{root}:{path}", "root": root, "path": path, "kind": kind}


def test_name_parts_join_folders_and_dotted_file_names():
    assert name_parts(_ds("acc/ledger.csv"), ROOTS) == ["acc", "ledger"]
    assert name_parts(_ds("Acc.Ledger.csv"), ROOTS) == ["acc", "ledger"]
    assert name_parts(_ds("hive/acc/ledger", "folder"), ROOTS) == ["hive", "acc", "ledger"]
    assert name_parts(_ds(".", "folder"), ROOTS) == ["landing"]


def test_rules_in_order():
    tables = ["acc.ledger", "cat.acc.daily", "calendar", "a.events", "b.events", "{}.orders"]
    datasets = [_ds("x/acc/ledger", "folder"), _ds("cat/acc/daily.parquet"), _ds("calendar.csv"),
                _ds("events.jsonl"), _ds("orders.csv"), _ds("tmp/scratch.csv"), _ds("mapped.csv")]

    result = match(datasets, tables, {"d0:mapped.csv": ["mart.revenue"]}, ROOTS)

    assert result["matches"] == [
        {"table": "acc.ledger", "dataset": "d0:x/acc/ledger", "status": "inferred", "rule": "path"},
        {"table": "calendar", "dataset": "d0:calendar.csv", "status": "inferred", "rule": "table_name"},
        {"table": "cat.acc.daily", "dataset": "d0:cat/acc/daily.parquet", "status": "inferred", "rule": "path"},
        {"table": "mart.revenue", "dataset": "d0:mapped.csv", "status": "verified", "rule": "map"},
        {"table": "{}.orders", "dataset": "d0:orders.csv", "status": "inferred", "rule": "table_name"},
    ]
    assert result["unresolved"] == [{"dataset": "d0:events.jsonl", "reason": "ambiguous", "candidates": ["a.events", "b.events"]}]
    assert result["orphan_datasets"] == ["d0:tmp/scratch.csv"]
    assert result["tables_without_data"] == ["a.events", "b.events"]


def test_a_fixed_schema_does_not_match_another_schema_by_path():
    result = match([_ds("other/ledger.csv")], ["acc.ledger"], {}, ROOTS)

    assert result["matches"][0]["rule"] == "table_name"


def _map(tmp_path, tables, version=1):
    path = tmp_path / "data-map.json"
    path.write_text(json.dumps({"version": version, "tables": tables}), encoding="utf-8")
    return path


def test_the_map_names_datasets_with_or_without_a_folder_id(tmp_path):
    ids = ["d0:a/b", "d0:c.csv"]

    assert load_map(_map(tmp_path, {"S.T": "a/b", "x.y": "d0:c.csv"}), ["d0"], ids) == {"d0:a/b": ["s.t"], "d0:c.csv": ["x.y"]}


@pytest.mark.parametrize("tables, roots, message", [
    ({"s.t": "missing"}, ["d0"], "not a dataset"),
    ({"s.t": "../x"}, ["d0"], "inside its data folder"),
    ({"s.t": "a/b"}, ["d0", "d1"], "data folder id"),
    ({"s.t": 3}, ["d0"], "must map"),
])
def test_bad_map_entries_name_the_table(tmp_path, tables, roots, message):
    with pytest.raises(UsageError, match=message):
        load_map(_map(tmp_path, tables), roots, ["d0:a/b"])


def test_a_map_of_another_version_is_refused(tmp_path):
    with pytest.raises(UsageError, match="version"):
        load_map(_map(tmp_path, {}, version=2), ["d0"], [])


def test_a_map_that_cannot_be_read_is_a_usage_error(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    binary = tmp_path / "bad.json"
    binary.write_bytes(b"\xff\xfe\x00{")

    for path in (folder, binary, tmp_path / "missing.json"):
        with pytest.raises(UsageError, match="data map"):
            load_map(path, ["d0"], [])
