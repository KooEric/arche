"""Report HTML for profiled data: one block per matched table, then the loose ends."""
from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Callable, Dict, List

Link = Callable[[str], str]
Translate = Callable[[str], str]
MAX_COLUMNS_SHOWN = 200
PERCENT_DIGITS = 1


def _code(text) -> str:
    return f"<code>{escape(str(text))}</code>"


def _range(column: dict) -> str:
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


def _columns(dataset: dict, T: Translate) -> str:
    columns = dataset.get("columns", [])
    rows = []
    for column in columns[:MAX_COLUMNS_SHOWN]:
        null = f'{round(column["null_ratio"] * 100, PERCENT_DIGITS)}%' if "null_ratio" in column else ""
        distinct = (f'≥{column["distinct"]}' if column.get("distinct_capped") else str(column["distinct"])) if "distinct" in column else ""
        patterns = ", ".join(_code(pattern) for pattern, _ in column.get("patterns", [])[:3])
        values = ", ".join(_code(value) for value, _ in column.get("top_values", []))
        rows.append(f'<tr><td>{_code(column["name"])} {_flags(column, T)}</td><td>{escape(column["type"])}</td>'
                    f'<td>{null}</td><td>{distinct}</td><td>{_range(column)}</td><td>{patterns or values}</td></tr>')
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
    if dataset.get("malformed_rows"):
        notes.append(T("{count} malformed rows").format(count=dataset["malformed_rows"]))
    return "".join(f'<p class="callout" data-tone="warn">{note}</p>' for note in notes)


def _rows(dataset: dict) -> str:
    total = dataset.get("rows_total")
    read = dataset.get("rows_read")
    if total is not None and read is not None and total != read:
        return f"{read:,} / {total:,}"
    return f"{total if total is not None else read or 0:,}"


def _table_block(table: str, found: List[tuple], writers: Dict[str, List[str]], readers: Dict[str, List[str]],
                 link: Link, T: Translate) -> str:
    match, dataset = found[0]
    keys = [column["name"] for column in dataset.get("columns", []) if column.get("candidate_key")]
    summary = (f'{_code(table)} <span class="badge" data-status="{match["status"]}">{T(match["status"])}</span> '
               f'· {T("rows")} {_rows(dataset)} · {T("columns")} {len(dataset.get("columns", []))}'
               + (f' · {T("key")} {", ".join(_code(key) for key in keys[:3])}' if keys else ""))
    lineage = (f'<p>{T("Written by")}: {", ".join(link(node) for node in writers.get(table, [])) or T("none scanned")}</p>'
               f'<p>{T("Read by")}: {", ".join(link(node) for node in readers.get(table, [])) or T("none scanned")}</p>')
    sources = "".join(f'<p>{T("Data")}: {_code(item["id"])} <span class="badge" data-status="{m["status"]}">{escape(m["rule"])}</span></p>'
                      for m, item in found)
    return (f'<details class="group table-data"><summary>{summary}</summary>{lineage}{sources}'
            f'{_notes(dataset, T)}{_columns(dataset, T)}</details>')


def table_data(graph: dict, profile: dict, link: Link, T: Translate) -> str:
    """The data part of the Tables section."""
    datasets = {item["id"]: item for item in profile["datasets"]}
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
    by_table: Dict[str, List[tuple]] = defaultdict(list)
    for match in profile["matches"]:
        by_table[match["table"]].append((match, datasets[match["dataset"]]))
    profiled = sum(1 for item in profile["datasets"] if item["status"] == "profiled")
    lead = (f'<p>{T("Datasets")} {len(datasets)} · {T("profiled")} {profiled} · {T("matched tables")} {len(by_table)} · '
            f'{T("tables without data")} {len(profile["tables_without_data"])} · '
            f'{T("datasets the code does not name")} {len(profile["orphan_datasets"]) + len(profile["unresolved"])}</p>')
    blocks = "".join(_table_block(table, by_table[table], writers, readers, link, T) for table in sorted(by_table))
    missing = "".join(f"<li>{_code(table)}</li>" for table in profile["tables_without_data"])
    loose = [f"<li>{_code(item)}</li>" for item in profile["orphan_datasets"]]
    loose += [f'<li>{_code(item["dataset"])} · {_code(item["reason"])} · {", ".join(_code(c) for c in item["candidates"])}</li>'
              for item in profile["unresolved"]]
    return (f'<h3>{T("Table data")} <span class="count">{len(by_table)}</span></h3>{lead}{blocks}'
            + (f'<h3>{T("Tables without data")} <span class="count">{len(profile["tables_without_data"])}</span></h3><ul>{missing}</ul>' if missing else "")
            + (f'<h3>{T("Datasets the code does not name")} <span class="count">{len(loose)}</span></h3><ul>{"".join(loose)}</ul>' if loose else ""))
