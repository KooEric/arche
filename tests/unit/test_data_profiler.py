"""profile_dataset on its own: every Parquet reader path and the row limit."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/arche/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from arche_lib.data.discover import discover
from arche_lib.data.profiler import profile_dataset
from parquet_footer import BINARY, I32, parquet_file

SCHEMA = [
    [(4, BINARY, "schema"), (5, I32, 2)],
    [(1, I32, 2), (4, BINARY, "id")],
    [(1, I32, 6), (4, BINARY, "name"), (6, I32, 0)],
]


def _only(root):
    datasets, _, _ = discover(root, "d0")
    assert len(datasets) == 1
    return datasets[0]


def _parquet_folder(tmp_path):
    root = tmp_path / "data"
    for part in ("1", "2"):
        folder = root / "t" / ("dt=" + part)
        folder.mkdir(parents=True)
        (folder / "part-0.parquet").write_bytes(parquet_file(SCHEMA, 2))
    return _only(root)


def _rows(path, limit):
    yield {"id": 1, "name": "a"}
    yield {"id": 2, "name": "b"}


def _failing(error):
    def reader(path, limit):
        raise error
        yield  # pragma: no cover
    return reader


def test_parquet_without_a_reader_is_schema_only(tmp_path):
    (tmp_path / "cols.parquet").write_bytes(parquet_file(SCHEMA, 7))

    result = profile_dataset(_only(tmp_path), 100, False, None)

    assert (result["status"], result["reason"], result["reader"]) == ("schema_only", "no_reader", "stdlib")
    assert result["rows_total"] == 7
    assert result["columns"][0]["name"] == "id"
    assert all({"type", "physical_type", "logical_type"} <= set(c) for c in result["columns"])


def test_stub_reader_profiles_rows_and_adds_partitions(tmp_path):
    result = profile_dataset(_parquet_folder(tmp_path), 100, False, ("pyarrow", _rows))

    assert (result["status"], result["reader"]) == ("profiled", "library")
    assert result["rows_read"] == 4 and result["rows_total"] == 4
    columns = {c["name"]: c for c in result["columns"]}
    assert columns["id"]["physical_type"] == "INT64" and columns["name"]["physical_type"] == "BYTE_ARRAY"
    assert "dt" in columns


def test_reader_exception_falls_back_to_schema_only(tmp_path):
    result = profile_dataset(_parquet_folder(tmp_path), 100, False, ("duckdb", _failing(RuntimeError("boom"))))

    assert (result["status"], result["reason"], result["error"]) == ("schema_only", "reader_failed", "RuntimeError")
    assert "boom" not in str(result)


def test_reader_oserror_is_unreadable_not_reader_failed(tmp_path):
    result = profile_dataset(_parquet_folder(tmp_path), 100, False, ("pyarrow", _failing(PermissionError("no"))))

    assert (result["status"], result["reason"]) == ("error", "unreadable")


def test_row_limit_hit_only_when_more_rows_exist(tmp_path):
    (tmp_path / "a.csv").write_text("x\n1\n2\n3\n", encoding="utf-8")
    dataset = _only(tmp_path)

    exact = profile_dataset(dataset, 3, False, None)
    short = profile_dataset(dataset, 2, False, None)

    assert exact["row_limit_hit"] is False and exact["rows_read"] == 3 and exact["rows_total"] == 3
    assert short["row_limit_hit"] is True and short["rows_read"] == 2 and "rows_total" not in short
