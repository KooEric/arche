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

from . import clean_text

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
# A name is sensitive when any entry is a substring of it once separators are removed, after the known harmless words
# below are blanked out. Every word in HARMLESS_WORDS must contain an entry and must not itself be sensitive;
# add a word only for a real false positive. Blanking leaves a marker, so what was around a harmless word never fuses
# into an entry (companytel stays sensitive through "tel"; discard_card through "card"). "userid" names one person and
# "accessedat" times when they were somewhere; either one joined with other columns can single a person out.
SENSITIVE_NAMES = ("email", "phone", "mobile", "tel", "ssn", "passport", "password", "passwd", "secret", "token",
                   "apikey", "card", "pan", "ccnum", "cardno", "iban", "accountno", "birth", "dob", "address",
                   "socialsecurity", "userid", "accessedat", "주민", "전화", "휴대", "생년", "주소", "계좌", "여권")
HARMLESS_WORDS = (
    "company", "companies", "span", "spanish", "japan", "japanese", "panel", "panda",
    "expand", "expanded", "pancake",
    "hotel", "intel", "telemetry", "television", "teleport", "motel", "cartel", "pastel",
    "adobe",
    "discard", "discarded", "scorecard", "wildcard",
    "phonetic", "tokenize", "tokenizer", "tokenized", "tokenization", "cardinal", "cardinality",
)
# No plain plurals (spans, hotels): blanking the singular leaves a harmless "s", while blanking the plural could
# swallow the "s" that starts the next entry (spanssn would lose "ssn").
_HARMLESS_LONGEST_FIRST = tuple(sorted(HARMLESS_WORDS, key=len, reverse=True))
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")
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
    spaced = _CAMEL_BOUNDARY.sub(" ", re.sub(r"[\s_.\-]+", " ", name)).lower()
    for word in _HARMLESS_LONGEST_FIRST:
        spaced = spaced.replace(word, "|")
    remaining = re.sub(r"\s+", "", spaced)
    return any(part in remaining for part in SENSITIVE_NAMES)


def _is_phone(text: str) -> bool:
    return bool(_PHONE.fullmatch(text)) and 9 <= sum(char.isdigit() for char in text) <= 15


def contact_shaped(text: str) -> bool:
    """An email address or a phone number."""
    return bool(_EMAIL.fullmatch(text)) or _is_phone(text)


def luhn(text: str) -> bool:
    """A 13-19 digit text that passes the Luhn check: the shape of a payment card number."""
    if not (13 <= len(text) <= 19 and text.isascii() and text.isdigit()):
        return False
    total = 0
    for index, char in enumerate(reversed(text)):
        digit = int(char)
        if index % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


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
        self.checked = 0   # non-null values examined for a sensitive shape
        self.shaped = 0    # of those, card-, email- or phone-shaped
        self.extremes: Dict[str, list] = {}   # group -> [min key, min text, max key, max text]

    def add(self, value: Any) -> None:
        self.present += 1
        kind, key = classify(value)
        if kind == "null":
            self.nulls += 1
            return
        self.types[kind] += 1
        self.checked += 1
        text = clean_text(value if isinstance(value, str) else str(value))
        if len(self.hashes) < DISTINCT_LIMIT:
            self.hashes.add(hashlib.sha1(f"{kind}\0{text}".encode("utf-8", "replace")).digest())
        elif not self.capped:
            self.capped = hashlib.sha1(f"{kind}\0{text}".encode("utf-8", "replace")).digest() not in self.hashes
        if self.show_values:
            _bounded_add(self.top, text[:VALUE_CHARS])
        if isinstance(value, str):
            self.lengths = [min(self.lengths[0], len(value)), max(self.lengths[1], len(value))] if self.lengths else [len(value), len(value)]
        is_text = kind == "string" and isinstance(value, str)
        if is_text:
            _bounded_add(self.patterns, shape(text))
        # Digit-only 9-15 digit integers are not treated as phones (an id or an amount looks the same);
        # a phone is a string, or sits in a column whose name says so. Card numbers are caught by Luhn.
        counts = (is_text and contact_shaped(value)) or (
            (isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))) and luhn(text))
        if counts:
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
        sensitive = sensitive_name(self.name) or (self.checked > 0 and self.shaped / self.checked >= SENSITIVE_SHARE)
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
