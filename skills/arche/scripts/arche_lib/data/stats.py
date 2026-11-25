"""Per-column aggregates in one pass. Keeps counts, hashes and short shapes, not values."""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
import string
from collections import Counter
from decimal import Decimal, InvalidOperation
from itertools import groupby
from typing import Any, Dict, List, Optional, Tuple

TYPE_SHARE = 0.95
DISTINCT_LIMIT = 10_000
TOP = 5
VALUE_CHARS = 40
PATTERN_CHARS = 40
RUN_LIMIT = 4
SENSITIVE_SHARE = 0.5
RATIO_DIGITS = 4
TRACKED_KEYS = 10_000
RANGED = ("integer", "decimal", "date", "timestamp")
NULL_TEXT = frozenset({"", "NULL", "null", "\\N", "NaN", "nan"})
SENSITIVE_NAMES = ("email", "phone", "mobile", "tel", "ssn", "passport", "password", "passwd", "secret",
                   "token", "apikey", "card", "iban", "accountno", "birth", "dob", "address",
                   "주민", "전화", "휴대", "생년", "주소", "계좌", "여권")
_INTEGER = re.compile(r"[+-]?\d{1,18}")
_LEADING_ZERO = re.compile(r"[+-]?0\d+")
_DECIMAL = re.compile(r"[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIMESTAMP = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:\d{2})?")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE = re.compile(r"\+?[\d\-() ]+")
_KEEP = set(string.punctuation) | {" "}


def shape(text: str) -> str:
    """A for A-Z, a for a-z, 9 for 0-9, ? for other letters; runs over RUN_LIMIT become x{n}."""
    marks = []
    for char in text:
        if "A" <= char <= "Z":
            marks.append("A")
        elif "a" <= char <= "z":
            marks.append("a")
        elif "0" <= char <= "9":
            marks.append("9")
        elif char in _KEEP:
            marks.append(char)
        else:
            marks.append("?")
    runs = []
    for mark, group in groupby(marks):
        count = len(list(group))
        runs.append(f"{mark}{{{count}}}" if count > RUN_LIMIT else mark * count)
    return "".join(runs)[:PATTERN_CHARS]


def _date(text: str) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def classify_text(text: str) -> Tuple[str, Any]:
    """(type, sort key) of one text value. The key orders values of that type."""
    if text in NULL_TEXT:
        return "null", None
    lowered = text.lower()
    if lowered in ("true", "false"):
        return "boolean", None
    if _LEADING_ZERO.fullmatch(text):
        return "string", None
    if _INTEGER.fullmatch(text):
        return "integer", Decimal(text)
    if _DECIMAL.fullmatch(text):
        try:
            return "decimal", Decimal(text)
        except InvalidOperation:
            return "string", None
    if _DATE.fullmatch(text) and _date(text):
        return "date", text
    found = _TIMESTAMP.fullmatch(text)
    if found and _date(found.group(1)) and int(found.group(2)) < 24 and int(found.group(3)) < 60:
        return "timestamp", f"{found.group(1)} {found.group(2)}:{found.group(3)}:{found.group(4) or '00'}"
    return "string", None


def classify(value: Any) -> Tuple[str, Any]:
    """(type, sort key) of a value from any reader."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "null", None
    if isinstance(value, str):
        return classify_text(value)
    if isinstance(value, bool):
        return "boolean", None
    if isinstance(value, int):
        return "integer", Decimal(value)
    if isinstance(value, (float, Decimal)):
        return "decimal", Decimal(repr(value)) if isinstance(value, float) else value
    if isinstance(value, dt.datetime):
        return "timestamp", value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, dt.date):
        return "date", value.isoformat()
    if isinstance(value, dict):
        return "object", None
    if isinstance(value, (list, tuple)):
        return "array", None
    if isinstance(value, (bytes, bytearray)):
        return "binary", None
    return "string", None


def sensitive_name(name: str) -> bool:
    normalized = re.sub(r"[\s_.\-]", "", name.lower())
    return any(part in normalized for part in SENSITIVE_NAMES)


def _is_phone(text: str) -> bool:
    return bool(_PHONE.fullmatch(text)) and 9 <= sum(char.isdigit() for char in text) <= 15


def _bounded_add(counter: Counter, key: str) -> None:
    if key in counter or len(counter) < TRACKED_KEYS:
        counter[key] += 1


class ColumnStats:
    """Aggregates for one column. add() each present value, then summary(rows_read)."""

    def __init__(self, name: str, show_values: bool) -> None:
        self.name = name
        self.show_values = show_values
        self.present = 0
        self.nulls = 0
        self.types: Counter = Counter()
        self.hashes: set = set()
        self.capped = False
        self.lengths: Optional[List[int]] = None
        self.patterns: Counter = Counter()
        self.top: Counter = Counter()
        self.texts = 0
        self.shaped = 0
        self.extremes: Dict[str, list] = {}   # group -> [min key, min text, max key, max text]

    def add(self, value: Any) -> None:
        self.present += 1
        kind, key = classify(value)
        if kind == "null":
            self.nulls += 1
            return
        self.types[kind] += 1
        text = value if isinstance(value, str) else str(value)
        if len(self.hashes) < DISTINCT_LIMIT:
            self.hashes.add(hashlib.sha1(f"{kind}\0{text}".encode("utf-8", "replace")).digest())
        elif not self.capped:
            self.capped = hashlib.sha1(f"{kind}\0{text}".encode("utf-8", "replace")).digest() not in self.hashes
        if self.show_values:
            _bounded_add(self.top, text[:VALUE_CHARS])
        if isinstance(value, str):
            self.lengths = [min(self.lengths[0], len(value)), max(self.lengths[1], len(value))] if self.lengths else [len(value), len(value)]
        if kind == "string" and isinstance(value, str):
            self.texts += 1
            _bounded_add(self.patterns, shape(value))
            if _EMAIL.fullmatch(value) or _is_phone(value):
                self.shaped += 1
        if key is not None:
            group = "number" if kind in ("integer", "decimal") else kind
            current = self.extremes.get(group)
            if current is None:
                self.extremes[group] = [key, text, key, text]
            else:
                if key < current[0]:
                    current[0], current[1] = key, text
                if key > current[2]:
                    current[2], current[3] = key, text

    def _type(self) -> Tuple[str, Optional[list]]:
        counts = Counter(self.types)
        if counts["integer"] and counts["decimal"]:
            counts["decimal"] += counts.pop("integer")
        total = sum(counts.values())
        if not total:
            return "unknown", None
        kind, count = counts.most_common(1)[0]
        if count / total >= TYPE_SHARE:
            return kind, None
        return "mixed", [[name, round(share / total, RATIO_DIGITS)] for name, share in counts.most_common(3)]

    def summary(self, rows_read: int) -> dict:
        kind, mix = self._type()
        nulls = self.nulls + (rows_read - self.present)
        distinct = len(self.hashes)
        sensitive = sensitive_name(self.name) or (self.texts > 0 and self.shaped / self.texts >= SENSITIVE_SHARE)
        result = {
            "name": self.name, "type": kind,
            "null_ratio": round(nulls / rows_read, RATIO_DIGITS) if rows_read else 0.0,
            "distinct": distinct, "distinct_capped": self.capped,
            "length": self.lengths,
            "patterns": [[pattern, count] for pattern, count in sorted(self.patterns.items(), key=lambda item: (-item[1], item[0]))[:TOP]],
            "candidate_key": nulls == 0 and not self.capped and distinct == rows_read and rows_read >= 2,
            "sensitive": sensitive,
        }
        if mix:
            result["types"] = mix
        group = "number" if kind in ("integer", "decimal") else kind
        if kind in RANGED and group in self.extremes and not sensitive:
            result["min"], result["max"] = self.extremes[group][1], self.extremes[group][3]
        if self.show_values and not sensitive:
            result["top_values"] = [[value, count] for value, count in sorted(self.top.items(), key=lambda item: (-item[1], item[0]))[:TOP]]
        return result
