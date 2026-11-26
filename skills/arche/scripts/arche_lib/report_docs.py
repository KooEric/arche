"""Report HTML for dbt model tables: what the docs say next to what the data shows.

Documented columns and profiled columns are joined by name, ignoring case. A test
hint counts what the sample shows (nulls under `not_null`, repeats under `unique`);
it never shows a value and is not a test result.
"""
from __future__ import annotations

import re
from html import escape
from typing import Callable, Dict, List, Optional

Link = Callable[[str], str]
Translate = Callable[[str], str]
Cells = Callable[[dict], str]
FIRST_SENTENCE_CHARS = 120
MAX_COLUMNS_SHOWN = 200
PERCENT_DIGITS = 1
DBT_CAVEAT = ("A dbt model's schema is chosen by the target at run time, "
              "so links from other SQL and from data to a model table are inferred.")
HINT_CAVEAT = "Test hints come from the sample read and are not test results."
# dbt data_type -> the profile's column type; types not listed are not compared.
TYPE_GROUPS = {
    **dict.fromkeys(("int", "integer", "bigint", "smallint", "tinyint", "int64"), "integer"),
    **dict.fromkeys(("numeric", "decimal", "number", "float", "float64", "double", "real"), "decimal"),
    **dict.fromkeys(("varchar", "char", "string", "text"), "string"),
    "date": "date",
    **dict.fromkeys(("timestamp", "timestamp_ntz", "timestamp_tz", "timestamp_ltz", "datetime"), "timestamp"),
    **dict.fromkeys(("bool", "boolean"), "boolean"),
}
PROFILE_TYPES = frozenset(TYPE_GROUPS.values())
_SENTENCE_END = re.compile(r"[.!?。](?=\s|$)")


def _code(text) -> str:
    return f"<code>{escape(str(text))}</code>"


def first_sentence(text: Optional[str]) -> str:
    """The first sentence of a description on one line, at most FIRST_SENTENCE_CHARS characters."""
    flat = " ".join((text or "").split())
    end = _SENTENCE_END.search(flat)
    sentence = flat[:end.end()] if end else flat
    return sentence if len(sentence) <= FIRST_SENTENCE_CHARS else sentence[:FIRST_SENTENCE_CHARS - 1] + "…"


def documented_type(data_type: Optional[str]) -> Optional[str]:
    """The profile type a dbt data_type stands for: `varchar(20)` -> string; None when not mapped."""
    if not data_type:
        return None
    return TYPE_GROUPS.get(data_type.strip().lower().split("(", 1)[0].strip())


def summary(model: dict) -> str:
    """The `dbt` badge and the model description's first sentence, for the table block's summary line."""
    sentence = first_sentence(model["description"])
    return '<span class="badge" data-tone="ok">dbt</span>' + (f' <span class="label">{escape(sentence)}</span>' if sentence else "")


def _test_label(test: dict) -> str:
    """A test name with its main arguments, all escaped."""
    label = _code(test["name"])
    if "values_count" in test:
        label += f' ({", ".join(_code(value) for value in test["values"])}{" …" if test["values_count"] > len(test["values"]) else ""})'
    to = test.get("to", {})
    if "ref" in to:
        label += f' → {_code(to["ref"])}'
    elif "source" in to:
        label += f' → {_code(".".join(to["source"]))}'
    if "field" in test:
        label += f'.{_code(test["field"])}'
    return label


def details(model: dict, link: Link, T: Translate) -> str:
    """Model file, property file, doc block, model tests and duplicate entries."""
    where = model["defined_in"]
    parts = [f'{T("Model file")}: {link(model["file"])}']
    if where:
        parts.append(f'{T("Documented in")}: {link("file:" + where["path"])}:{where["line"]:d}')
    if model["doc"]:
        parts.append(f'{T("doc block")}: {_code(model["doc"])}')
    rows = [f'<p>{" · ".join(parts)}</p>']
    if model["tests"]:
        rows.append(f'<p>{T("Model tests")}: {", ".join(_test_label(test) for test in model["tests"])}</p>')
    if model["duplicates"]:
        rows.append(f'<p>{T("Also documented in")}: '
                    + ", ".join(f'{link("file:" + item["path"])}:{item["line"]:d}' for item in model["duplicates"]) + "</p>")
    return "".join(rows)


def _hints(column: Optional[dict], tests: List[dict], rows_read: int, T: Translate) -> List[str]:
    """Counts the sample shows against `not_null` and `unique`; never a value."""
    if column is None:
        return []
    names = {test["name"] for test in tests}
    found = []
    ratio = column.get("null_ratio", 0.0)
    if "not_null" in names and ratio > 0:
        found.append(T("In the sample: null {ratio}").format(ratio=f"{round(ratio * 100, PERCENT_DIGITS)}%"))
    if ("unique" in names and "distinct" in column and not column.get("distinct_capped") and ratio == 0
            and column["distinct"] < rows_read):
        found.append(T("In the sample: duplicates"))
    return found


def _badge(text: str, tone: str) -> str:
    return f'<span class="badge" data-tone="{tone}">{text}</span>'


def columns(model: dict, dataset: Optional[dict], cells: Cells, flags: Cells, T: Translate) -> str:
    """One table of documented and profiled columns joined by name, with type differences and test hints."""
    data = dataset.get("columns", []) if dataset else []
    by_name: Dict[str, dict] = {}
    for column in data:
        by_name.setdefault(column["name"].lower(), column)
    rows, used, hinted = [], set(), False
    rows_read = (dataset or {}).get("rows_read") or 0
    for documented in model["columns"]:
        column = by_name.get(documented["name"].lower())
        if column is not None:
            used.add(id(column))
        marks = [flags(column)] if column else []
        if dataset is not None and column is None:
            marks.append(_badge(T("Not in data"), "warn"))
        expected = documented_type(documented["data_type"])
        if column and expected and column["type"] in PROFILE_TYPES and column["type"] != expected:
            marks.append(_badge(T("Type differs"), "warn"))
        hints = _hints(column, documented["tests"], rows_read, T)
        hinted = hinted or bool(hints)
        tests = ", ".join(_test_label(test) for test in documented["tests"])
        tests += "".join(f'<br><span class="badge" data-tone="warn">{hint}</span>' for hint in hints)
        rows.append(f'<tr><td>{_code(documented["name"])} {" ".join(mark for mark in marks if mark)}</td>'
                    f'<td>{escape(documented["data_type"] or "")}</td><td>{escape(column["type"]) if column else ""}</td>'
                    f'<td>{escape(documented["description"] or "")}</td><td>{tests}</td>'
                    f'{cells(column) if column else "<td></td>" * 4}</tr>')
    for column in data:
        if id(column) in used:
            continue
        rows.append(f'<tr><td>{_code(column["name"])} {flags(column)} {_badge(T("Not in docs"), "warn")}</td><td></td>'
                    f'<td>{escape(column["type"])}</td><td></td><td></td>{cells(column)}</tr>')
    hidden = len(rows) - MAX_COLUMNS_SHOWN
    more = f'<p class="empty">{T("+{count} more columns").format(count=hidden)}</p>' if hidden > 0 else ""
    head = "".join(f"<th>{T(name)}</th>" for name in (
        "Column", "Documented type", "Data type", "Description", "Tests", "Null", "Distinct", "Range", "Shape"))
    caveat = f'<p class="label">{T(HINT_CAVEAT)}</p>' if hinted else ""
    return (f'<table class="data-columns docs-columns"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows[:MAX_COLUMNS_SHOWN])}</tbody></table>{more}{caveat}')
