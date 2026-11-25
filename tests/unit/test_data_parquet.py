"""Parquet footers with the standard library; rows only through a library the user has."""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/arche/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from arche_lib.data import parquet_meta, parquet_rows
from arche_lib.data.parquet_meta import column_type, read_footer
from arche_lib.errors import ParseError
from parquet_footer import BINARY, BOOL, I32, I64, LIST, MAP, STRUCT, parquet_file, struct, wrap

SCHEMA = [
    [(4, BINARY, "schema"), (5, I32, 3)],
    [(1, I32, 2), (4, BINARY, "id")],
    [(4, BINARY, "user"), (5, I32, 2)],
    [(1, I32, 6), (4, BINARY, "name"), (6, I32, 0)],
    [(1, I32, 1), (4, BINARY, "born"), (10, STRUCT, [(6, STRUCT, [])])],
    [(1, I32, 2), (4, BINARY, "amount"), (6, I32, 5)],
]


def test_footer_gives_row_count_and_leaf_columns(tmp_path):
    path = tmp_path / "a.parquet"
    path.write_bytes(parquet_file(SCHEMA, 42))

    footer = read_footer(path)

    assert footer["rows"] == 42
    assert footer["columns"] == [
        {"name": "id", "physical_type": "INT64", "logical_type": ""},
        {"name": "user.name", "physical_type": "BYTE_ARRAY", "logical_type": "STRING"},
        {"name": "user.born", "physical_type": "INT32", "logical_type": "DATE"},
        {"name": "amount", "physical_type": "INT64", "logical_type": "DECIMAL"},
    ]
    assert [column_type(c) for c in footer["columns"]] == ["integer", "string", "date", "decimal"]


@pytest.mark.parametrize("content, reason", [
    (b"PAR1", "not_parquet"),
    (b"XXXX" + b"\x00" * 8 + b"PAR1", "not_parquet"),
    (b"PAR1" + b"\x00" * 4 + (999).to_bytes(4, "little") + b"PAR1", "not_parquet"),
    (b"PAR1" + b"\xff" * 10 + (10).to_bytes(4, "little") + b"PAR1", "parquet_bad_type"),
    (b"PAR1" + b"\x19" + (1).to_bytes(4, "little") + b"PAR1", "parquet_footer_truncated"),
])
def test_broken_files_raise_a_reason(tmp_path, content, reason):
    path = tmp_path / "a.parquet"
    path.write_bytes(content)

    with pytest.raises(ParseError, match=reason):
        read_footer(path)


def test_oversized_footers_and_deep_nesting_are_refused(tmp_path, monkeypatch):
    path = tmp_path / "a.parquet"
    path.write_bytes(parquet_file(SCHEMA, 1))
    monkeypatch.setattr(parquet_meta, "MAX_FOOTER", 4)
    with pytest.raises(ParseError, match="footer_too_large"):
        read_footer(path)
    monkeypatch.setattr(parquet_meta, "MAX_FOOTER", 1 << 24)
    monkeypatch.setattr(parquet_meta, "MAX_DEPTH", 1)
    with pytest.raises(ParseError, match="parquet_too_deep"):
        read_footer(path)


def test_no_library_means_no_row_reader(monkeypatch):
    monkeypatch.setattr(parquet_rows, "_load", lambda name: None)

    assert parquet_rows.find_reader() is None


def test_pyarrow_is_preferred_and_stops_at_the_limit(monkeypatch):
    converted = []

    class Batch:
        def __init__(self, rows):
            self.rows = rows

        def slice(self, offset, length):
            return Batch(self.rows[offset:offset + length])

        def to_pylist(self):
            converted.append(len(self.rows))
            return self.rows

    closed = []
    handle = types.SimpleNamespace(iter_batches=lambda batch_size: [Batch([{"a": 1}, {"a": 2}, {"a": 3}])],
                                   close=lambda: closed.append(True))
    parquet = types.SimpleNamespace(ParquetFile=lambda path: handle)
    monkeypatch.setattr(parquet_rows, "_load", lambda name: parquet if name == "pyarrow.parquet" else object())

    name, read = parquet_rows.find_reader()

    assert name == "pyarrow" and list(read(Path("x.parquet"), 2)) == [{"a": 1}, {"a": 2}]
    assert converted == [2] and closed == [True]


def test_pyarrow_file_is_closed_when_the_reader_stops_early(monkeypatch):
    closed = []
    batch = types.SimpleNamespace(slice=lambda o, n: batch, to_pylist=lambda: [{"a": 1}, {"a": 2}])
    handle = types.SimpleNamespace(iter_batches=lambda batch_size: iter([batch, batch]),
                                   close=lambda: closed.append(True))
    parquet = types.SimpleNamespace(ParquetFile=lambda path: handle)
    monkeypatch.setattr(parquet_rows, "_load", lambda name: parquet if name == "pyarrow.parquet" else None)

    _, read = parquet_rows.find_reader()
    rows = read(Path("x.parquet"), 5)
    next(rows)
    rows.close()

    assert closed == [True]


def test_a_broken_pyarrow_install_falls_back_to_duckdb(monkeypatch):
    def broken(name):
        if name == "pyarrow.parquet":
            raise RuntimeError("binary incompatibility")
        return object()

    monkeypatch.setattr(parquet_rows.importlib, "import_module", broken)

    name, _ = parquet_rows.find_reader()

    assert name == "duckdb"


def test_duckdb_gets_the_path_as_a_parameter(monkeypatch):
    calls = []

    class Cursor:
        description = [("a",), ("b",)]
        chunks = [[(1, 2)], []]

        def fetchmany(self, size):
            return self.chunks.pop(0)

    class Connection:
        def execute(self, sql, params):
            calls.append((sql, params))
            return Cursor()

        def close(self):
            calls.append("closed")

    duckdb = types.SimpleNamespace(connect=lambda target: Connection())
    monkeypatch.setattr(parquet_rows, "_load", lambda name: duckdb if name == "duckdb" else None)

    name, read = parquet_rows.find_reader()

    assert name == "duckdb" and list(read(Path("/d/x.parquet"), 5)) == [{"a": 1, "b": 2}]
    assert calls[0][1] == ["/d/x.parquet", 5] and "/d/x" not in calls[0][0] and calls[-1] == "closed"


def test_real_pyarrow_reads_rows(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    import pyarrow
    path = tmp_path / "a.parquet"
    pq.write_table(pyarrow.table({"a": [1, 2]}), str(path))

    name, read = parquet_rows.find_reader()

    assert list(read(path, 10)) == [{"a": 1}, {"a": 2}]
    assert read_footer(path)["rows"] == 2


def test_unused_fields_are_skipped_without_being_built(tmp_path):
    extra = [
        (4, LIST, (STRUCT, [[]] * 200000)),
        (5, LIST, (BOOL, [True, False, True])),
        (6, MAP, (I32, BOOL, [(1, True), (2, False)])),
        (7, MAP, (I32, I32, [])),
        (8, BOOL, True),
    ]
    path = tmp_path / "a.parquet"
    path.write_bytes(parquet_file(SCHEMA, 42, extra))

    footer = read_footer(path)
    data = path.read_bytes()
    length = int.from_bytes(data[-8:-4], "little")
    parsed = parquet_meta._Reader(data[-8 - length:-8]).fields(0, keep=(2, 3))

    assert footer["rows"] == 42 and [c["name"] for c in footer["columns"]] == ["id", "user.name", "user.born", "amount"]
    assert sorted(parsed) == [2, 3]


def test_deeply_nested_skipped_fields_are_still_refused(tmp_path):
    nested = (STRUCT, [[]])
    for _ in range(100):
        nested = (LIST, [nested])
    path = tmp_path / "a.parquet"
    path.write_bytes(parquet_file(SCHEMA, 1, [(4, LIST, nested)]))

    with pytest.raises(ParseError, match="parquet_too_deep"):
        read_footer(path)


@pytest.mark.parametrize("rows", [-1, True])
def test_bad_row_counts_are_a_schema_error(tmp_path, rows):
    fields = [(2, LIST, (STRUCT, SCHEMA)), (3, BOOL, True)] if rows is True else [(2, LIST, (STRUCT, SCHEMA)), (3, I64, rows)]
    path = tmp_path / "a.parquet"
    path.write_bytes(wrap(struct(fields)))

    with pytest.raises(ParseError, match="parquet_bad_schema"):
        read_footer(path)


def test_an_endless_varint_is_refused(tmp_path):
    footer = bytes([0x15]) + b"\xff" * 11 + b"\x01"
    path = tmp_path / "a.parquet"
    path.write_bytes(wrap(footer))

    with pytest.raises(ParseError, match="parquet_bad_varint"):
        read_footer(path)
