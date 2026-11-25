"""Profile one dataset: read its rows up to a limit and summarize every column."""
from __future__ import annotations

from typing import Dict, Iterator, List, Optional, Tuple

from ..errors import ParseError
from . import parquet_meta, readers
from .discover import Dataset, DataFile, restat
from .parquet_rows import RowReader
from .stats import ColumnStats

SKIP_REASONS = frozenset({"json_too_large"})


def _rows(files: List[DataFile], fmt: str, stats: readers.ReadStats,
          parquet: Optional[RowReader], limit: int) -> Iterator[dict]:
    for item in files:
        source = parquet(item.path, limit) if fmt == "parquet" else readers.rows(item.path, fmt, stats)
        for row in source:
            if fmt == "parquet":
                row = readers.flatten(row)
            for key, value in item.partition:
                row.setdefault(key, value)
            yield row


def _parquet_schema(files: List[DataFile]) -> Tuple[int, List[dict], bool]:
    """(total rows, union of leaf columns in first-seen order, schema differs between files)."""
    total, columns, names, drift, first = 0, [], set(), False, None
    for item in files:
        footer = parquet_meta.read_footer(item.path)
        total += footer["rows"]
        current = [column["name"] for column in footer["columns"]]
        if first is None:
            first = current
        elif current != first:
            drift = True
        for column in footer["columns"]:
            if column["name"] not in names:
                names.add(column["name"])
                columns.append(column)
    return total, columns, drift


def _schema_only(record: dict, schema: List[dict], total: int, drift: bool, reason: str) -> dict:
    columns = [{"name": column["name"], "type": parquet_meta.column_type(column),
                "physical_type": column["physical_type"], "logical_type": column["logical_type"]}
               for column in schema]
    return {**record, "status": "schema_only", "reason": reason, "reader": "stdlib", "rows_total": total,
            "schema_drift": drift, "columns": columns}


def _scan(dataset: Dataset, limit: int, show_values: bool, parquet: Optional[RowReader]) -> dict:
    record, fmt = dataset.record, dataset.record["format"]
    stats = readers.ReadStats()
    columns: Dict[str, ColumnStats] = {}
    read, hit = 0, False
    for row in _rows(dataset.files, fmt, stats, parquet, limit + 1):
        if read >= limit:
            hit = True  # one more row exists past the limit
            break
        read += 1
        for name, value in row.items():
            column = columns.get(name)
            if column is None:
                column = columns[name] = ColumnStats(name, show_values)
            column.add(value)
    return {"rows_read": read, "row_limit_hit": hit, "malformed_rows": stats.malformed_rows,
            "encoding_errors": stats.encoding_errors, "schema_drift": stats.schema_drift,
            "header_fixed": dict(sorted(stats.header_fixed.items())),
            "columns": [column.summary(read) for column in columns.values()]}


def profile_dataset(dataset: Dataset, limit: int, show_values: bool,
                    parquet: Optional[Tuple[str, RowReader]]) -> dict:
    """The dataset record with status, reader, row counts and column summaries."""
    record = dataset.record
    try:
        if record["format"] == "parquet":
            total, schema, drift = _parquet_schema(dataset.files)
            if parquet is None:
                return _schema_only(record, schema, total, drift, "no_reader")
            name, reader = parquet
            try:
                scanned = _scan(dataset, limit, show_values, reader)
            except Exception as exc:  # a third-party reader may raise anything
                return {**_schema_only(record, schema, total, drift, "reader_failed"), "error": type(exc).__name__}
            types = {column["name"]: column for column in schema}
            for column in scanned["columns"]:
                if column["name"] in types:
                    column["physical_type"] = types[column["name"]]["physical_type"]
                    column["logical_type"] = types[column["name"]]["logical_type"]
            result = {**record, "status": "profiled", "reader": name, "rows_total": total,
                      **scanned, "schema_drift": drift or scanned["schema_drift"]}
        else:
            scanned = _scan(dataset, limit, show_values, None)
            result = {**record, "status": "profiled", "reader": "stdlib", **scanned}
            if not scanned["row_limit_hit"]:
                result["rows_total"] = scanned["rows_read"]
    except ParseError as exc:
        reason = str(exc)
        return {**record, "status": "skipped" if reason in SKIP_REASONS else "error", "reason": reason}
    except OSError:
        return {**record, "status": "error", "reason": "unreadable"}
    if restat(dataset.files) != record["fingerprint"]:
        return {**record, "status": "error", "reason": "changed_while_reading"}
    return result
