"""Report HTML for profiled data and dbt model tables: one block per table, then the loose ends."""
from __future__ import annotations

from collections import defaultdict
from functools import partial
from html import escape
from typing import Callable, Dict, List, Optional

from . import report_docs
from .data import MAX_COLUMNS

Link = Callable[[str], str]
Translate = Callable[[str], str]
MAX_COLUMNS_SHOWN = 200
PERCENT_DIGITS = 1
CAVEAT = ("Counts describe the rows read, up to the row limit. "
          "A table matched by path or table name is inferred; check it before relying on it.")

TRUNCATED = ("Discovery stopped at the file or dataset limit; "
             "tables listed without data may have data that was not read.")


def _code(text) -> str:
    return f"<code>{escape(str(text))}</code>"


def _range(column: dict) -> str:
    if column.get("sensitive"):
        return ""
    if "min" in column:
        return f'{_code(column["min"])} – {_code(column["max"])}'
    if column.get("length"):
        low, high = column["length"]
        return f"len {low}–{high}"
    return ""


def _flags(column: dict, T: Translate) -> str:
    flags = []
    if column.get("candidate_key"):
        flags.append(f'<span class="badge" data-tone="ok">{T("key")}</span>')
    if column.get("sensitive"):
        flags.append(f'<span class="badge" data-tone="warn">{T("sensitive")}</span>')
    return " ".join(flags)


def _metric_cells(column: dict) -> str:
    """The Null, Distinct, Range and Shape cells of one profiled column."""
    null = f'{round(column["null_ratio"] * 100, PERCENT_DIGITS)}%' if "null_ratio" in column else ""
    distinct = (f'≥{column["distinct"]}' if column.get("distinct_capped") else str(column["distinct"])) if "distinct" in column else ""
    patterns = ", ".join(_code(pattern) for pattern, _ in column.get("patterns", [])[:3])
    values = "" if column.get("sensitive") else ", ".join(_code(value) for value, _ in column.get("top_values", []))
    shape = " · ".join(part for part in (patterns, values) if part)
    return f'<td>{null}</td><td>{distinct}</td><td>{_range(column)}</td><td>{shape}</td>'


def _columns(dataset: dict, T: Translate) -> str:
    columns = dataset.get("columns", [])
    rows = []
    for column in columns[:MAX_COLUMNS_SHOWN]:
        rows.append(f'<tr><td>{_code(column["name"])} {_flags(column, T)}</td><td>{escape(column["type"])}</td>'
                    f'{_metric_cells(column)}</tr>')
    hidden = len(columns) - MAX_COLUMNS_SHOWN
    more = f'<p class="empty">{T("+{count} more columns").format(count=hidden)}</p>' if hidden > 0 else ""
    head = "".join(f"<th>{T(name)}</th>" for name in ("Column", "Type", "Null", "Distinct", "Range", "Shape"))
    return f'<table class="data-columns"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>{more}'


def _notes(dataset: dict, T: Translate) -> str:
    notes = []
    if dataset["status"] == "schema_only":
        notes.append(T("Schema only: install pyarrow or duckdb to read rows."))
    if dataset["status"] in ("error", "skipped"):
        notes.append(f'{T("Not read")}: {_code(dataset.get("reason", ""))}')
    if dataset.get("row_limit_hit"):
        notes.append(T("Read up to the row limit; counts describe a sample."))
    if dataset.get("schema_drift"):
        notes.append(T("Files in this dataset have different columns."))
    if dataset.get("header_suspect"):
        notes.append(T("The first row or some keys looked like data, so columns were renamed column_N or key_N."))
    if dataset.get("columns_truncated"):
        notes.append(T("Only the first {count} columns were profiled.").format(count=MAX_COLUMNS))
    if dataset.get("malformed_rows"):
        notes.append(T("{count} malformed rows").format(count=dataset["malformed_rows"]))
    return "".join(f'<p class="callout" data-tone="warn">{note}</p>' for note in notes)


def _rows(dataset: dict) -> str:
    total = dataset.get("rows_total")
    read = dataset.get("rows_read")
    if total is not None and read is not None and total != read:
        return f"{read:,} / {total:,}"
    return f"{total if total is not None else read or 0:,}"


def _summary(table: str, found: List[tuple], model: Optional[dict], T: Translate) -> str:
    docs = f" {report_docs.summary(model)}" if model else ""
    if not found:
        return f"{_code(table)}{docs}"
    match, dataset = found[0]
    status = escape(match["status"], quote=True)
    keys = [column["name"] for column in dataset.get("columns", []) if column.get("candidate_key")]
    return (f'{_code(table)}{docs} <span class="badge" data-status="{status}">{escape(T(match["status"]))}</span> '
            f'· {T("rows")} {_rows(dataset)} · {T("columns")} {len(dataset.get("columns", []))}'
            + (f' · {T("key")} {", ".join(_code(key) for key in keys[:3])}' if keys else ""))


def _table_block(table: str, found: List[tuple], writers: Dict[str, List[str]], readers: Dict[str, List[str]],
                 link: Link, T: Translate, model: Optional[dict] = None) -> str:
    lineage = (f'<p>{T("Written by")}: {", ".join(link(node) for node in writers.get(table, [])) or T("none scanned")}</p>'
               f'<p>{T("Read by")}: {", ".join(link(node) for node in readers.get(table, [])) or T("none scanned")}</p>')
    flags = partial(_flags, T=T)
    if model:
        lineage += report_docs.details(model, link, T)
    sources = "".join(f'<div class="dataset"><p>{T("Data")}: {_code(item["id"])} '
                      f'<span class="badge" data-status="{escape(m["status"], quote=True)}">{escape(str(m["rule"]))}</span></p>'
                      f'{_notes(item, T)}'
                      f'{report_docs.columns(model, item, _metric_cells, flags, T) if model else _columns(item, T)}</div>'
                      for m, item in found)
    if model and not found:
        sources = report_docs.columns(model, None, _metric_cells, flags, T)
    return f'<details class="group table-data"><summary>{_summary(table, found, model, T)}</summary>{lineage}{sources}</details>'


def _model_tables(graph: dict) -> Dict[str, dict]:
    """Table name -> the first dbt model building it."""
    found: Dict[str, dict] = {}
    for model in graph.get("dbt_models", []):
        if model["table"]:
            found.setdefault(model["table"], model)
    return found


def has_tables(graph: dict, profile: Optional[dict]) -> bool:
    """True when the Tables section has table blocks to show."""
    return bool(profile) or bool(_model_tables(graph))


def table_data(graph: dict, profile: Optional[dict], link: Link, T: Translate) -> str:
    """The table blocks of the Tables section: profiled data, dbt model docs, or both."""
    models = _model_tables(graph)
    if profile is None:
        blocks = "".join(_table_block(table, [], *_lineage(graph), link, T, models[table]) for table in sorted(models))
        return (f'<h3>{T("dbt model tables")} <span class="count">{len(models)}</span></h3>'
                f'<p>{T(report_docs.DBT_CAVEAT)}</p>{blocks}')
    return _profiled(graph, profile, models, link, T)


def _lineage(graph: dict) -> tuple:
    """(table -> writing files, table -> reading files)."""
    writers: Dict[str, List[str]] = defaultdict(list)
    for item in graph.get("written_tables", []):
        writers[item["table"]].append(item["from"])
    readers: Dict[str, List[str]] = defaultdict(list)
    for edge in graph["edges"]:
        if edge.get("kind") == "READS" and edge["from"] not in readers[edge["table"]]:
            readers[edge["table"]].append(edge["from"])
    for item in graph.get("external_tables", []):
        if item["from"] not in readers[item["table"]]:
            readers[item["table"]].append(item["from"])
    return writers, readers


def _profiled(graph: dict, profile: dict, models: Dict[str, dict], link: Link, T: Translate) -> str:
    datasets = {item["id"]: item for item in profile["datasets"]}
    writers, readers = _lineage(graph)
    by_table: Dict[str, List[tuple]] = defaultdict(list)
    for match in profile["matches"]:
        by_table[match["table"]].append((match, datasets[match["dataset"]]))
    profiled = sum(1 for item in profile["datasets"] if item["status"] == "profiled")
    lead = (f'<p>{T("Datasets")} {len(datasets)} · {T("profiled")} {profiled} · {T("matched tables")} {len(by_table)} · '
            f'{T("tables without data")} {len(profile["tables_without_data"])} · '
            f'{T("datasets the code does not name")} {len(profile["orphan_datasets"]) + len(profile["unresolved"])}</p>')
    blocks = "".join(_table_block(table, by_table.get(table, []), writers, readers, link, T, models.get(table))
                     for table in sorted(set(by_table) | set(models)))
    missing = "".join(f"<li>{_code(table)}</li>" for table in profile["tables_without_data"])
    loose = [f"<li>{_code(item)}</li>" for item in profile["orphan_datasets"]]
    loose += [f'<li>{_code(item["dataset"])} · {_code(item["reason"])} · {", ".join(_code(c) for c in item["candidates"])}</li>'
              for item in profile["unresolved"]]
    cut = (f'<p class="callout" data-tone="warn">{T(TRUNCATED)}</p>' if profile.get("truncated") else "")
    dbt_caveat = f'<p>{T(report_docs.DBT_CAVEAT)}</p>' if models else ""
    return (f'<h3>{T("Table data")} <span class="count">{len(set(by_table) | set(models))}</span></h3>'
            f'<p>{T(CAVEAT)}</p>{dbt_caveat}{cut}{lead}{blocks}'
            + (f'<h3>{T("Tables without data")} <span class="count">{len(profile["tables_without_data"])}</span></h3><ul>{missing}</ul>' if missing else "")
            + (f'<h3>{T("Datasets the code does not name")} <span class="count">{len(loose)}</span></h3><ul>{"".join(loose)}</ul>' if loose else ""))
