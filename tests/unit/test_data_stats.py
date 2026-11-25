"""Column aggregates: types, nulls, distinct counts, ranges, shapes and sensitivity."""
import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/arche/scripts"))
from arche_lib.data import stats as module
from arche_lib.data.stats import ColumnStats, classify, sensitive_name, shape


def _summary(name, values, rows=None, show_values=False):
    column = ColumnStats(name, show_values)
    for value in values:
        column.add(value)
    return column.summary(len(values) if rows is None else rows)


@pytest.mark.parametrize("value, kind", [
    ("", "null"), ("NULL", "null"), ("\\N", "null"), ("nan", "null"), (None, "null"), (float("nan"), "null"),
    ("TRUE", "boolean"), (True, "boolean"), ("42", "integer"), ("-7", "integer"), (3, "integer"),
    ("007", "string"), ("0", "integer"), ("1" * 19, "decimal"), ("1.5", "decimal"), ("1e5", "decimal"),
    (2.5, "decimal"), (Decimal("1.1"), "decimal"), ("2026-11-25", "date"), ("2026-02-30", "string"),
    (dt.date(2026, 1, 1), "date"), ("2026-11-25T10:00:00Z", "timestamp"), ("2026-11-25 25:00", "string"),
    (dt.datetime(2026, 1, 1, 1), "timestamp"), ({"a": 1}, "object"), ([1], "array"), (b"x", "binary"),
    ("Kim", "string"),
])
def test_value_types(value, kind):
    assert classify(value)[0] == kind


def test_a_type_needs_95_percent_of_values_and_integers_merge_into_decimals():
    assert _summary("n", ["1"] * 19 + ["x"])["type"] == "integer"
    mixed = _summary("n", ["1"] * 9 + ["x"])
    assert mixed["type"] == "mixed" and mixed["types"] == [["integer", 0.9], ["string", 0.1]]
    assert _summary("n", ["1", "2.5"])["type"] == "decimal"
    assert _summary("n", [None, ""])["type"] == "unknown"


def test_nulls_include_rows_where_the_column_is_missing():
    result = _summary("n", ["1", ""], rows=4)

    assert result["null_ratio"] == 0.75 and result["candidate_key"] is False


def test_ranges_compare_by_type_and_keep_the_original_text():
    result = _summary("n", ["10", "9", "1e1", "-2"])
    assert (result["min"], result["max"]) == ("-2", "10")
    dates = _summary("d", ["2026-11-02", "2026-01-15"])
    assert (dates["min"], dates["max"]) == ("2026-01-15", "2026-11-02")
    assert "min" not in _summary("s", ["b", "a"])


def test_candidate_keys_need_distinct_non_null_values_and_two_rows():
    assert _summary("id", ["1", "2", "3"])["candidate_key"] is True
    assert _summary("id", ["1", "1"])["candidate_key"] is False
    assert _summary("id", ["1"])["candidate_key"] is False


def test_distinct_counts_stop_at_the_limit(monkeypatch):
    monkeypatch.setattr(module, "DISTINCT_LIMIT", 3)
    result = _summary("id", [str(i) for i in range(5)])

    assert (result["distinct"], result["distinct_capped"], result["candidate_key"]) == (3, True, False)


def test_shapes_hide_letters_and_digits():
    assert shape("kim.hong@example.com") == "aaa.aaaa@a{7}.aaa"
    assert shape("AB-1234 김") == "AA-9999 ?"
    assert len(shape("x" * 100 + "-" * 100)) <= 40


def test_string_values_appear_only_as_shapes_unless_asked():
    hidden = _summary("code", ["AB-1", "AB-2", "CD-3"])
    assert hidden["patterns"] == [["AA-9", 3]] and "top_values" not in hidden

    shown = _summary("code", ["AB-1", "AB-1", "CD-3"], show_values=True)
    assert shown["top_values"] == [["AB-1", 2], ["CD-3", 1]]


@pytest.mark.parametrize("name", ["email", "User_Email", "phone_no", "api-key", "birth_date", "고객주소", "card"])
def test_sensitive_names(name):
    assert sensitive_name(name) is True


def test_sensitive_columns_never_carry_values_or_ranges():
    by_name = _summary("birth_date", ["2000-01-01", "1990-05-05"], show_values=True)
    assert "min" not in by_name and "top_values" not in by_name and by_name["sensitive"] is True

    by_value = _summary("contact", ["a@x.com", "b@y.org", "010-1234-5678", "n/a"], show_values=True)
    assert by_value["sensitive"] is True and "top_values" not in by_value
    assert all("@x.com" not in pattern for pattern, _ in by_value["patterns"])
