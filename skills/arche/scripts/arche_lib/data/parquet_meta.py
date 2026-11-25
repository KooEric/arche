"""Parquet schema and row count from the file footer, read with the standard library only.

The footer is a Thrift compact-protocol FileMetaData struct. Input is untrusted:
every length and nesting depth is checked before use."""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..errors import ParseError

MAGIC = b"PAR1"
MAX_FOOTER = 16 * 1024 * 1024
MAX_DEPTH = 64
_TRUE, _FALSE, _BYTE, _I16, _I32, _I64, _DOUBLE, _BINARY, _LIST, _SET, _MAP, _STRUCT = range(1, 13)
PHYSICAL = {0: "BOOLEAN", 1: "INT32", 2: "INT64", 3: "INT96", 4: "FLOAT", 5: "DOUBLE",
            6: "BYTE_ARRAY", 7: "FIXED_LEN_BYTE_ARRAY"}
CONVERTED = {0: "STRING", 1: "MAP", 3: "LIST", 4: "ENUM", 5: "DECIMAL", 6: "DATE", 7: "TIME", 8: "TIME",
             9: "TIMESTAMP", 10: "TIMESTAMP", 11: "INTEGER", 12: "INTEGER", 13: "INTEGER", 14: "INTEGER",
             15: "INTEGER", 16: "INTEGER", 17: "INTEGER", 18: "INTEGER", 19: "JSON", 20: "BSON", 21: "INTERVAL"}
LOGICAL = {1: "STRING", 2: "MAP", 3: "LIST", 4: "ENUM", 5: "DECIMAL", 6: "DATE", 7: "TIME", 8: "TIMESTAMP",
           10: "INTEGER", 11: "NULL", 12: "JSON", 13: "BSON", 14: "UUID", 15: "FLOAT16"}
_LOGICAL_TYPES = {"STRING": "string", "ENUM": "string", "JSON": "string", "UUID": "string", "DATE": "date",
                  "TIMESTAMP": "timestamp", "DECIMAL": "decimal", "INTEGER": "integer", "FLOAT16": "decimal"}
_PHYSICAL_TYPES = {"BOOLEAN": "boolean", "INT32": "integer", "INT64": "integer", "INT96": "timestamp",
                   "FLOAT": "decimal", "DOUBLE": "decimal"}


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def byte(self) -> int:
        if self.pos >= len(self.data):
            raise ParseError("parquet_footer_truncated")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def take(self, count: int) -> bytes:
        if count < 0 or self.pos + count > len(self.data):
            raise ParseError("parquet_footer_truncated")
        chunk = self.data[self.pos:self.pos + count]
        self.pos += count
        return chunk

    def varint(self) -> int:
        result = shift = 0
        while True:
            value = self.byte()
            result |= (value & 0x7F) << shift
            if not value & 0x80:
                return result
            shift += 7
            if shift > 63:
                raise ParseError("parquet_bad_varint")

    def zigzag(self) -> int:
        value = self.varint()
        return (value >> 1) ^ -(value & 1)

    def size(self, count: int) -> int:
        if count > len(self.data) - self.pos:
            raise ParseError("parquet_footer_truncated")
        return count

    def value(self, kind: int, depth: int) -> Any:
        if depth > MAX_DEPTH:
            raise ParseError("parquet_too_deep")
        if kind == _BYTE:
            return struct.unpack("b", self.take(1))[0]
        if kind in (_I16, _I32, _I64):
            return self.zigzag()
        if kind == _DOUBLE:
            return struct.unpack("<d", self.take(8))[0]
        if kind == _BINARY:
            return self.take(self.varint())
        if kind in (_LIST, _SET):
            return self.collection(depth)
        if kind == _MAP:
            return self.mapping(depth)
        if kind == _STRUCT:
            return self.fields(depth)
        raise ParseError("parquet_bad_type")

    def skip(self, kind: int, depth: int) -> None:
        """Advance past a value of any kind without building it; same bounds checks as value()."""
        if depth > MAX_DEPTH:
            raise ParseError("parquet_too_deep")
        if kind == _BYTE:
            self.take(1)
        elif kind in (_I16, _I32, _I64):
            self.varint()
        elif kind == _DOUBLE:
            self.take(8)
        elif kind == _BINARY:
            self.take(self.varint())
        elif kind in (_LIST, _SET):
            header = self.byte()
            count, element = header >> 4, header & 0x0F
            if count == 15:
                count = self.varint()
            self.skip_elements(element, self.size(count), depth + 1)
        elif kind == _MAP:
            count = self.size(self.varint())
            if count:
                kinds = self.byte()
                self.skip_elements(kinds >> 4, count, depth + 1)
                self.skip_elements(kinds & 0x0F, count, depth + 1)
        elif kind == _STRUCT:
            self.fields(depth, keep=())
        else:
            raise ParseError("parquet_bad_type")

    def skip_elements(self, kind: int, count: int, depth: int) -> None:
        if kind in (_TRUE, _FALSE):
            self.take(count)
            return
        for _ in range(count):
            self.skip(kind, depth)

    def element(self, kind: int, depth: int) -> Any:
        """A collection element: booleans take one byte here, unlike in a field header."""
        return self.byte() == 1 if kind in (_TRUE, _FALSE) else self.value(kind, depth)

    def fields(self, depth: int, keep: Optional[Iterable[int]] = None) -> Dict[int, Any]:
        """Read a struct. With keep, only those field ids are built; every other field is skipped."""
        found: Dict[int, Any] = {}
        last = 0
        while True:
            header = self.byte()
            kind = header & 0x0F
            if kind == 0:
                return found
            delta = header >> 4
            field = last + delta if delta else self.zigzag()
            if keep is not None and field not in keep:
                if kind not in (_TRUE, _FALSE):
                    self.skip(kind, depth + 1)
            else:
                found[field] = (kind == _TRUE) if kind in (_TRUE, _FALSE) else self.value(kind, depth + 1)
            last = field

    def collection(self, depth: int) -> List[Any]:
        header = self.byte()
        count, kind = header >> 4, header & 0x0F
        if count == 15:
            count = self.varint()
        return [self.element(kind, depth + 1) for _ in range(self.size(count))]

    def mapping(self, depth: int) -> List[tuple]:
        count = self.size(self.varint())
        if count == 0:
            return []
        kinds = self.byte()
        return [(self.element(kinds >> 4, depth + 1), self.element(kinds & 0x0F, depth + 1)) for _ in range(count)]


def _logical(element: Dict[int, Any]) -> str:
    union = element.get(10)
    if isinstance(union, dict) and union:
        return LOGICAL.get(min(union), "")
    return CONVERTED.get(element.get(6), "")


def _name(element: Dict[int, Any]) -> str:
    raw = element.get(4, b"")
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else ""


def leaves(elements: List[Any]) -> List[dict]:
    """Leaf columns of a depth-first schema list, named a.b.c from their groups."""
    columns: List[dict] = []
    stack: List[list] = []   # [name, children left]
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            raise ParseError("parquet_bad_schema")
        children = element.get(5) or 0
        if not isinstance(children, int) or children < 0:
            raise ParseError("parquet_bad_schema")
        if stack:
            stack[-1][1] -= 1
        if index > 0 and children == 0:
            columns.append({"name": ".".join([entry[0] for entry in stack[1:]] + [_name(element)]),
                            "physical_type": PHYSICAL.get(element.get(1), ""), "logical_type": _logical(element)})
        if children:
            stack.append([_name(element), children])
        while stack and stack[-1][1] <= 0:
            stack.pop()
    return columns


def column_type(column: dict) -> str:
    """The profile type name a schema-only column gets from its Parquet types."""
    logical = column.get("logical_type", "")
    if logical in _LOGICAL_TYPES:
        return _LOGICAL_TYPES[logical]
    return _PHYSICAL_TYPES.get(column.get("physical_type", ""), "string")


def read_footer(path: Path) -> dict:
    """{"rows": total row count, "columns": [{"name", "physical_type", "logical_type"}]}."""
    size = path.stat().st_size
    if size < 12:
        raise ParseError("not_parquet")
    with open(path, "rb") as handle:
        head = handle.read(4)
        handle.seek(size - 8)
        tail = handle.read(8)
        if head != MAGIC or tail[4:] != MAGIC:
            raise ParseError("not_parquet")
        length = int.from_bytes(tail[:4], "little")
        if length > MAX_FOOTER:
            raise ParseError("footer_too_large")
        if length > size - 12:
            raise ParseError("not_parquet")
        handle.seek(size - 8 - length)
        footer = handle.read(length)
    meta = _Reader(footer).fields(0, keep=(2, 3))   # row groups and statistics are never built
    rows = meta.get(3, 0)
    schema = meta.get(2, [])
    if not isinstance(rows, int) or isinstance(rows, bool) or rows < 0 or not isinstance(schema, list):
        raise ParseError("parquet_bad_schema")
    return {"rows": rows, "columns": leaves(schema)}
