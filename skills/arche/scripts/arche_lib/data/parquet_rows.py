"""Parquet rows through pyarrow or duckdb, only when the user's environment already has one.

Both are imported by name at run time, so the skill itself imports only the standard library."""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Callable, Iterator, Optional, Tuple

BATCH_ROWS = 8192
RowReader = Callable[[Path, int], Iterator[dict]]


def _load(name: str):
    try:
        return importlib.import_module(name)
    except Exception:   # a broken install can fail at import with more than ImportError
        return None


def _pyarrow(parquet) -> RowReader:
    def read(path: Path, limit: int) -> Iterator[dict]:
        handle = parquet.ParquetFile(str(path))
        produced = 0
        try:
            for batch in handle.iter_batches(batch_size=BATCH_ROWS):
                if produced >= limit:
                    return
                for row in batch.slice(0, limit - produced).to_pylist():
                    produced += 1
                    yield row
        finally:
            close = getattr(handle, "close", None)
            if close is not None:
                close()
    return read


def _duckdb(duckdb) -> RowReader:
    def read(path: Path, limit: int) -> Iterator[dict]:
        connection = duckdb.connect(":memory:")
        try:
            cursor = connection.execute("SELECT * FROM read_parquet(?, hive_partitioning = false) LIMIT ?",
                                        [str(path), limit])
            names = [column[0] for column in cursor.description]
            while True:
                chunk = cursor.fetchmany(BATCH_ROWS)
                if not chunk:
                    return
                for values in chunk:
                    yield dict(zip(names, values))
        finally:
            connection.close()
    return read


def find_reader() -> Optional[Tuple[str, RowReader]]:
    """("pyarrow" | "duckdb", reader) for the first library found, else None."""
    parquet = _load("pyarrow.parquet")
    if parquet is not None:
        return "pyarrow", _pyarrow(parquet)
    duckdb = _load("duckdb")
    if duckdb is not None:
        return "duckdb", _duckdb(duckdb)
    return None
