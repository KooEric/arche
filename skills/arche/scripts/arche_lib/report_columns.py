"""Report HTML for columns.json: a column lineage panel per table, what removing a column touches,
the columns not followed, and the column impact of a change. Every repository string is escaped."""
from __future__ import annotations

from collections import Counter, deque
from html import escape
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

Translate = Callable[[str], str]
PathLink = Callable[[str], str]  # repository path -> HTML for it, a link to its file card when it has one
MAX_COLUMNS_SHOWN = 200
MAX_LIST_SHOWN = 50
MAX_EXPRESSIONS_SHOWN = 3
MAX_EVIDENCE_SHOWN = 5
IMPACT_BUDGET = 5000  # edges one column's walk may visit, as in changes.json
REPORT_IMPACT_BUDGET = 2_000_000  # edges all walks of one report may visit together
USAGE_KINDS = ("filter", "join_key", "group_key", "order_key")
DEFINED_BY = ("sql_output", "ddl")
STATUS_ORDER = ("verified", "inferred", "unresolved")
STATEMENT_ORDER = ("parsed", "partial", "unsupported", "parse_error", "too_large")
CAVEATS = ("Column lineage reads only SELECT statements in SQL files. It does not follow SQL inside views or Python, "
           "names changed by ALTER, PIVOT, or macro output.",
           "An inferred link needs checking before you rely on it.")
TRUNCATED = "Column lineage stopped at an edge limit; some columns may have more sources and uses than shown."
REASONS = {
    "ambiguous": "Columns matching several sources",
    "unknown_star": "SELECT * from tables with unknown columns",
    "unknown_column": "Columns no source lists",
    "unknown_qualifier": "Qualifiers matching no source",
    "dynamic": "Names built at run time",
    "arity": "INSERT column counts that do not match",
    "unnamed": "Output columns without a name",
    "too_deep": "Expressions nested too deeply",
    "too_many_columns": "Statements with too many columns",
}


def _plain(text: str) -> str:
    return escape(text)


def _code(text) -> str:
    return f"<code>{escape(str(text))}</code>"


def _status(status: str) -> str:
    status = escape(status)
    return f'<span class="status" data-state="{status}">{status}</span>'


def _badge(kind: str) -> str:
    return f'<span class="badge" data-kind="{escape(kind, quote=True)}">{escape(kind)}</span>'


def _rank(status: str) -> int:
    return STATUS_ORDER.index(status) if status in STATUS_ORDER else len(STATUS_ORDER) - 1


def _name(node: Optional[str]) -> str:
    """`col:a.b` or `table:a` without its prefix; ids are never split further."""
    if not node:
        return ""
    return node.partition(":")[2]


def _where(evidence: dict, link: Optional[PathLink] = None) -> str:
    """`path:line` as one code span, or the path linked to its file card followed by the line."""
    path, line = str(evidence.get("path", "")), evidence.get("line", "")
    return _code(f"{path}:{line}") if link is None else f"{link(path)}:{escape(str(line))}"


def _path(path: str, link: Optional[PathLink] = None) -> str:
    return _code(path) if link is None else link(path)


def _more(hidden: int, T: Translate, cut: bool = False) -> str:
    if hidden > 0:
        text = T("+{count} or more entries") if cut else T("+{count} more entries")
        return f'<p class="empty">{text.format(count=hidden)}</p>'
    return f'<p class="empty">{T("Stopped at the walk limit.")}</p>' if cut else ""


def _unique(items: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(items))


def _capped(items: List[str], limit: int, T: Translate) -> str:
    shown = ", ".join(items[:limit])
    hidden = len(items) - limit
    return f'{shown}, {T("+{count} more entries").format(count=hidden)}' if hidden > 0 else shown


class ColumnLineage:
    """columns.json indexed once: value edges in and out of each column, uses per table, unresolved by reason."""

    def __init__(self, doc: dict, T: Translate = _plain, link: Optional[PathLink] = None) -> None:
        self.doc, self.T, self.path_link = doc, T, link
        self.columns: Dict[str, dict] = {column["id"]: column for column in doc["columns"]}
        self.incoming: Dict[str, List[dict]] = {}
        self.outgoing: Dict[str, List[dict]] = {}
        self.uses: Dict[str, List[dict]] = {}
        for edge in doc["edges"]:
            if edge["to"].startswith("table:"):
                self.uses.setdefault(_name(edge["to"]), []).append(edge)
            else:
                self.incoming.setdefault(edge["to"], []).append(edge)
            if edge.get("from"):
                self.outgoing.setdefault(edge["from"], []).append(edge)
        self.unresolved: Dict[str, List[dict]] = {}
        self.missed: Dict[str, List[dict]] = {}
        for item in doc["unresolved"]:
            self.unresolved.setdefault(item["reason"], []).append(item)
            if item.get("to"):
                self.missed.setdefault(item["to"], []).append(item)
        self.by_table: Dict[str, List[dict]] = {}
        for column in doc["columns"]:
            if set(column.get("known_from", ())) & set(DEFINED_BY) or column["id"] in self.incoming:
                self.by_table.setdefault(column["table"], []).append(column)
        self.anchors = {column["id"]: f"column-{index}" for index, column in enumerate(
            column for table in sorted(self.by_table) for column in self.by_table[table][:MAX_COLUMNS_SHOWN])}
        self.spent = 0

    def tables(self) -> List[str]:
        """Tables the SQL writes, which get a column lineage panel."""
        return sorted(self.by_table)

    def _link(self, node: Optional[str]) -> str:
        column = self.columns.get(node or "")
        label = _code(f'{column["table"]}.{column["display"]}' if column else _name(node))
        return f'<a href="#{self.anchors[node]}">{label}</a>' if node in self.anchors else label

    def panel(self, table: str) -> str:
        """The "Column lineage" block of one table, or "" when the SQL writes no column of it."""
        if table not in self.by_table:
            return ""
        return self._block(table, f'{self.T("Column lineage")} <span class="count">{len(self.by_table[table])}</span>')

    def _block(self, table: str, summary: str) -> str:
        T, columns = self.T, self.by_table[table]
        head = "".join(f"<th>{T(name)}</th>" for name in ("Column", "Sources", "Kind", "Expression", "Status", "Evidence"))
        rows = "".join(self._row(column) for column in columns[:MAX_COLUMNS_SHOWN])
        hidden = len(columns) - MAX_COLUMNS_SHOWN
        more = f'<p class="empty">{T("+{count} more columns").format(count=hidden)}</p>' if hidden > 0 else ""
        return (f'<details class="group column-lineage"><summary>{summary}</summary>'
                f'<table class="data-columns"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>'
                f'{more}{self._table_uses(table)}</details>')

    def _row(self, column: dict) -> str:
        edges = self.incoming.get(column["id"], [])
        missed = self.missed.get(column["id"], [])
        statuses = [edge["status"] for edge in edges] + ["unresolved"] * bool(missed)
        status = _status(max(statuses, key=_rank)) if statuses else ""
        kinds = " ".join(_badge(kind) for kind in sorted({edge["kind"] for edge in edges}))
        expressions = _unique(_code(edge["expression"]) for edge in edges if edge.get("expression"))
        evidence = _unique(_where(edge["evidence"], self.path_link) for edge in edges)
        if not edges and column.get("defined"):
            evidence = [_where(column["defined"], self.path_link)]
        anchor = self.anchors.get(column["id"])
        ident = f' id="{anchor}"' if anchor else ""
        name = (f'<details{ident}><summary>{_code(column["display"])}</summary>'
                f'<p class="label">{self.T("If this column is removed")}</p>{self._impact_of(column["id"])}</details>')
        return (f'<tr><td>{name}</td><td>{self._sources(edges, missed)}</td><td>{kinds}</td>'
                f'<td>{_capped(expressions, MAX_EXPRESSIONS_SHOWN, self.T)}</td><td>{status}</td>'
                f'<td>{_capped(evidence, MAX_EVIDENCE_SHOWN, self.T)}</td></tr>')

    def _sources(self, edges: List[dict], missed: List[dict]) -> str:
        T = self.T
        values = _unique(self._link(e["from"]) for e in edges if e.get("from") and e.get("role") != "condition")
        conditions = _unique(self._link(e["from"]) for e in edges if e.get("from") and e.get("role") == "condition")
        parts = [_capped(values, MAX_LIST_SHOWN, T)] if values else []
        if conditions:
            parts.append(f'{T("condition")}: {_capped(conditions, MAX_LIST_SHOWN, T)}')
        if any(edge["kind"] == "constant" for edge in edges):
            parts.append(T("constant"))
        if missed:
            parts.append(f'{T("not followed")}: ' + ", ".join(_unique(_code(item["reason"]) for item in missed)))
        return "<br>".join(parts)

    def _table_uses(self, table: str) -> str:
        uses = _unique(f'{_badge(edge["kind"])} {self._link(edge["from"])}' for edge in self.uses.get(table, []))
        if not uses:
            return ""
        return f'<p class="label">{self.T("Columns used to build this table")}</p>{_capped(uses, MAX_LIST_SHOWN, self.T)}'

    def _walk(self, start: str) -> Optional[Tuple[List[tuple], Set[str], Set[str], bool]]:
        """Breadth-first downstream of one column: value edges continue, uses stop. None past the report budget."""
        if self.spent >= REPORT_IMPACT_BUDGET:
            return None
        found: List[tuple] = []
        files: Set[str] = set()
        tables: Set[str] = set()
        seen, used = {start}, set()
        queue = deque([(start, 0, 0)])
        budget, cut = min(IMPACT_BUDGET, REPORT_IMPACT_BUDGET - self.spent), False
        while queue and not cut:
            node, depth, rank = queue.popleft()
            for edge in self.outgoing.get(node, ()):
                if budget <= 0:
                    cut = True
                    break
                budget -= 1
                self.spent += 1
                target, step = edge["to"], max(rank, _rank(edge["status"]))
                files.add(edge["evidence"].get("path", ""))
                if target.startswith("table:"):
                    if (target, edge["kind"]) not in used:
                        used.add((target, edge["kind"]))
                        tables.add(_name(target))
                        found.append((depth + 1, target, edge["kind"], step))
                elif target not in seen:
                    seen.add(target)
                    tables.add(self.columns[target]["table"] if target in self.columns else _name(target))
                    found.append((depth + 1, target, edge["kind"], step))
                    queue.append((target, depth + 1, step))
        return found, files, tables, cut

    def _impact_of(self, start: str) -> str:
        T, walked = self.T, self._walk(start)
        if walked is None:
            return f'<p class="empty">{T("Not computed: the report reached its impact limit.")}</p>'
        found, files, tables, cut = walked
        if not found:
            return f'<p class="empty">{T("No downstream column or use found.")}</p>'
        groups: Dict[int, List[str]] = {}
        for depth, target, kind, rank in found[:MAX_LIST_SHOWN]:
            used = target.startswith("table:")
            label = f'{T("used by")} {_code(_name(target))}' if used else self._link(target)
            groups.setdefault(depth, []).append(f'<li>{label} {_badge(kind)} {_status(STATUS_ORDER[rank])}</li>')
        body = "".join(f'<p class="label">{T("Depth {depth}").format(depth=depth)}</p><ul>{"".join(items)}</ul>'
                       for depth, items in groups.items())
        body += _more(len(found) - MAX_LIST_SHOWN, T, cut)
        body += f'<p>{T("Files")}: {_capped([_path(path, self.path_link) for path in sorted(files)], MAX_LIST_SHOWN, T)}</p>'
        return body + f'<p>{T("Tables")}: {_capped([_code(name) for name in sorted(tables)], MAX_LIST_SHOWN, T)}</p>'

    def section(self, shown_tables: Iterable[str] = ()) -> str:
        """Head of the Tables section: statement counts, caveats, blocks for tables not drawn elsewhere, and
        the columns not followed."""
        T, shown = self.T, set(shown_tables)
        counts = Counter(item["status"] for item in self.doc["statements"])
        order = [status for status in STATEMENT_ORDER if status in counts] + sorted(set(counts) - set(STATEMENT_ORDER))
        stats = " · ".join(f'{T(status)} {counts[status]}' for status in order)
        lead = f'<p>{T("SQL statements")} {len(self.doc["statements"])}' + (f" · {stats}" if stats else "") + "</p>"
        caveats = "".join(f"<p>{T(text)}</p>" for text in CAVEATS)
        cut = f'<p class="callout" data-tone="warn">{T(TRUNCATED)}</p>' if self.doc.get("truncated") else ""
        rest = [table for table in self.tables() if table not in shown]
        blocks = "".join(self._block(table, f'{_code(table)} <span class="count">{len(self.by_table[table])}</span>')
                         for table in rest)
        return (f'<h3>{T("Column lineage")} <span class="count">{len(self.by_table)}</span></h3>'
                f'{lead}{caveats}{cut}{blocks}{self._not_followed()}')

    def _not_followed(self) -> str:
        T, groups = self.T, []
        for reason in sorted(self.unresolved):
            items = self.unresolved[reason]
            title = REASONS.get(reason, "Tables not matched" if reason.startswith("table_") else "Other columns not followed")
            rows = "".join(f"<li>{self._missed(item)}</li>" for item in items[:MAX_COLUMNS_SHOWN])
            groups.append(f'<details class="group"><summary>{T(title)} {_code(reason)} <span class="count">{len(items)}</span>'
                          f'</summary><ul class="columns">{rows}</ul>{_more(len(items) - MAX_COLUMNS_SHOWN, T)}</details>')
        if not groups:
            return ""
        return (f'<h3>{T("Columns not followed")} <span class="count">{len(self.doc["unresolved"])}</span></h3>'
                + "".join(groups))

    def _missed(self, item: dict) -> str:
        parts = [_code(item.get("spec", ""))]
        if item.get("to"):
            parts.append(_code(_name(item["to"])))
        if item.get("source"):
            parts.append(f'{self.T("source")} {_code(item["source"])}')
        if item.get("candidates"):
            parts.append(f'{self.T("candidates")} ' + _capped([_code(name) for name in item["candidates"]], MAX_LIST_SHOWN, self.T))
        return " · ".join(parts + [_where(item.get("evidence", {}), self.path_link)])


def _listed(items: List[str], T: Translate) -> str:
    rows = "".join(f"<li>{item}</li>" for item in items[:MAX_LIST_SHOWN])
    return f'<ul class="columns">{rows}</ul>{_more(len(items) - MAX_LIST_SHOWN, T)}' if rows else ""


def _impact_rows(column_impact: dict, T: Translate, link: Optional[PathLink]) -> Tuple[List[str], List[str], List[str]]:
    seeds = [f'{_code(_name(item["column"]))} · {_badge(item["change"])} · {_path(item["path"], link)}'
             for item in column_impact.get("seeds", [])]
    affected = [f'{_code(_name(item["column"]))} · {T("Depth {depth}").format(depth=int(item["depth"]))} · '
                + " ".join(_badge(kind) for kind in item.get("kinds", []))
                + f' {_status(item["status"])} · ' + _capped([_path(path, link) for path in item.get("files", [])], MAX_LIST_SHOWN, T)
                for item in column_impact.get("affected", [])]
    used = [f'{_code(_name(item["table"]))} · ' + " ".join(_badge(kind) for kind in item.get("kinds", []))
            + f' {_status(item["status"])} · ' + _capped([_path(path, link) for path in item.get("files", [])], MAX_LIST_SHOWN, T)
            for item in column_impact.get("used_by", [])]
    return seeds, affected, used


def _unavailable(column_impact: dict, T: Translate) -> str:
    reason = column_impact.get("reason")
    if reason == "base_without_columns":
        why = T("The base snapshot has no column lineage; run imports.py on the base again.")
    else:
        why = f'{T("Reason")}: {_code(reason)}' if reason else ""
    return f'<p class="empty">{T("Column impact was not computed.")}</p>' + (f'<p class="empty">{why}</p>' if why else "")


def impact(column_impact: dict, T: Translate = _plain, link: Optional[PathLink] = None) -> str:
    """The column impact of a change, for the Changes section."""
    head = f'<h3>{T("Column impact")}</h3>'
    if not column_impact.get("available"):
        return head + _unavailable(column_impact, T)
    seeds, affected, used = _impact_rows(column_impact, T, link)
    kinds = sorted({item["seed"] for item in column_impact.get("seeds", [])})
    seed = f'<p>{T("Seed")}: ' + ", ".join(_code(kind) for kind in kinds) + "</p>" if kinds else ""
    body = (f'{head}<p class="lead">{T("Column-level results are exact only when two snapshots are compared.")}</p>{seed}'
            f'<p class="label">{T("Changed columns")} <span class="count">{len(seeds)}</span></p>{_listed(seeds, T)}'
            f'<p class="label">{T("Affected columns")} <span class="count">{len(affected)}</span></p>{_listed(affected, T)}'
            f'<p class="label">{T("Used by tables")} <span class="count">{len(used)}</span></p>{_listed(used, T)}')
    for title, key in (("Affected files", "affected_files"), ("Affected tables", "affected_tables")):
        names = [_code(name) for name in column_impact.get(key, [])]
        body += f'<p class="label">{T(title)} <span class="count">{len(names)}</span></p>{_listed(names, T)}'
    if column_impact.get("truncated"):
        body += f'<p class="callout" data-tone="warn">{T("Column impact stopped at its limit; more columns may be affected.")}</p>'
    return body
