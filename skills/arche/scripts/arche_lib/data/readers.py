"""Row readers for CSV, TSV, JSON and JSONL. Each yields one flat dict per row."""
from __future__ import annotations

import csv
import itertools
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from ..errors import ParseError
from . import clean_text
from .stats import classify_text, contact_shaped

SNIFF_CHARS = 64 * 1024
FIELD_LIMIT = 1024 * 1024
JSON_MAX_BYTES = 64 * 1024 * 1024
NESTING_DEPTH = 3
DELIMITERS = ",;|\t"
REPLACEMENT = "�"
JSONL_LINE_LIMIT = 1024 * 1024
HEADER_VALUE_SHARE = 0.5
VALUE_TYPES = frozenset({"integer", "decimal", "date", "timestamp"})


class ReadStats:
    """What a reader noticed while it yielded rows."""

    def __init__(self) -> None:
        self.malformed_rows = 0
        self.encoding_errors = 0
        self.schema_drift = False
        self.header_fixed: Dict[str, str] = {}
        self.headers: List[List[str]] = []
        self.header_suspect = False   # a header or key that looked like data was renamed
        self.suspect_keys: Dict[str, int] = {}

    def note_header(self, names: List[str], renamed: Dict[str, str]) -> None:
        if self.headers and names != self.headers[0]:
            self.schema_drift = True
        self.headers.append(names)
        self.header_fixed.update(renamed)


def fix_header(names: List[str]) -> tuple:
    """(names with blanks and repeats renamed, {new name: original})."""
    fixed: List[str] = []
    seen: Dict[str, int] = {}
    renamed: Dict[str, str] = {}
    for index, name in enumerate(names, 1):
        base = clean_text(name).strip() or f"column_{index}"
        seen[base] = seen.get(base, 0) + 1
        final = base if seen[base] == 1 else f"{base}_{seen[base]}"
        if final != name:
            renamed[final] = name
        fixed.append(final)
    return fixed, renamed


def _key(key: Any, stats: Optional[ReadStats]) -> str:
    """A JSON key as a column name part; an email- or phone-shaped key is a value, so it becomes key_<n>."""
    text = clean_text(str(key))
    if stats is None or not contact_shaped(text):
        return text
    stats.header_suspect = True
    return f"key_{stats.suspect_keys.setdefault(text, len(stats.suspect_keys) + 1)}"


def flatten(record: dict, depth: int = 1, prefix: str = "", stats: Optional[ReadStats] = None) -> Dict[str, Any]:
    """Nested objects become a.b.c columns up to NESTING_DEPTH; deeper objects stay whole."""
    flat: Dict[str, Any] = {}
    for key, value in record.items():
        name = f"{prefix}{_key(key, stats)}"
        if isinstance(value, dict) and depth < NESTING_DEPTH:
            flat.update(flatten(value, depth + 1, name + ".", stats))
        else:
            flat[name] = value
    return flat


def header_is_data(fields: List[str]) -> bool:
    """A first row that looks like values: a contact-shaped field, or mostly numbers and dates."""
    present = [field.strip() for field in fields if field.strip()]
    if any(contact_shaped(field) for field in present):
        return True
    typed = sum(1 for field in present if classify_text(field)[0] in VALUE_TYPES)
    return bool(present) and typed / len(present) > HEADER_VALUE_SHARE


def _delimiter(sample: str, fmt: str) -> str:
    if fmt == "tsv":
        return "\t"
    try:
        return csv.Sniffer().sniff(sample, delimiters=DELIMITERS).delimiter
    except csv.Error:
        return ","


def read_csv(path: Path, fmt: str, stats: ReadStats) -> Iterator[Dict[str, Any]]:
    csv.field_size_limit(FIELD_LIMIT)
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as handle:
        sample = handle.read(SNIFF_CHARS)
        handle.seek(0)
        reader = csv.reader(handle, delimiter=_delimiter(sample, fmt))
        try:
            header = next(reader, None)
            if header is None:
                return
            leading = []
            if header_is_data(header):
                names, renamed = [f"column_{index}" for index in range(1, len(header) + 1)], {}
                stats.header_suspect = True
                leading = [header]
            else:
                names, renamed = fix_header(header)
            stats.note_header(names, renamed)
            for row in itertools.chain(leading, reader):
                if not row:
                    continue
                if len(row) != len(names):
                    stats.malformed_rows += 1
                if any(REPLACEMENT in field for field in row):
                    stats.encoding_errors += 1
                yield dict(zip(names, row))
        except csv.Error as exc:
            raise ParseError("field_too_large" if "field larger" in str(exc) else "csv_error") from None


def read_json(path: Path, stats: ReadStats) -> Iterator[Dict[str, Any]]:
    if path.stat().st_size > JSON_MAX_BYTES:
        raise ParseError("json_too_large")
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        try:
            data = json.load(handle)
        except (json.JSONDecodeError, RecursionError):
            raise ParseError("json_error") from None
    if isinstance(data, dict):
        records = [data]
    elif isinstance(data, list) and all(isinstance(item, dict) for item in data):
        records = data
    else:
        raise ParseError("not_records")
    for record in records:
        yield flatten(record, stats=stats)


def read_jsonl(path: Path, stats: ReadStats) -> Iterator[Dict[str, Any]]:
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        while True:
            line = handle.readline(JSONL_LINE_LIMIT + 1)
            if not line:
                return
            if len(line) > JSONL_LINE_LIMIT:
                raise ParseError("line_too_large")
            if not line.strip():
                continue
            if REPLACEMENT in line:
                stats.encoding_errors += 1
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, RecursionError):
                record = None
            if not isinstance(record, dict):
                stats.malformed_rows += 1
                continue
            yield flatten(record, stats=stats)


def rows(path: Path, fmt: str, stats: ReadStats) -> Iterator[Dict[str, Any]]:
    """Rows of one csv, tsv, json or jsonl file. Raises ParseError with a reason code."""
    if fmt in ("csv", "tsv"):
        return read_csv(path, fmt, stats)
    if fmt == "json":
        return read_json(path, stats)
    if fmt == "jsonl":
        return read_jsonl(path, stats)
    raise ValueError(f"not a text format: {fmt}")
