"""The columns.json document: column nodes, edges, unresolved references and statement states,
sorted so that the same input gives the same bytes."""
from __future__ import annotations

from bisect import bisect_left
from typing import Dict, List, NamedTuple, Optional, Tuple

from .sqlcol import COLUMNS_VERSION, MAX_EDGES, MAX_EXPRESSION_CHARS, MAX_FILE_EDGES
from .sqlcol.scope import Leaf, Miss, cap


class TempWrite(NamedTuple):
    """A temporary table's statement: no column nodes, but its uses and misses count against `tables`."""
    name: str
    misses: Tuple[str, ...]  # reasons the statement is only partly mapped
    tables: Tuple[str, ...]  # tables written by later statements that read it; `temp:<name>` when none
    table: str = ""  # writes no table of its own


def newline_positions(text: str) -> List[int]:
    return [i for i, char in enumerate(text) if char == "\n"]


class _Places:
    """Evidence for one file: one shared dict per line."""

    def __init__(self, path: str, text: str, newlines: List[int]) -> None:
        self.path, self.lines = path, text.split("\n")
        self.newlines = newlines
        self.cache: Dict[int, dict] = {}

    def line(self, position: int) -> int:
        return bisect_left(self.newlines, position) + 1

    def at(self, position: int) -> dict:
        line = self.line(position)
        if line not in self.cache:
            quote = self.lines[line - 1].strip()[:MAX_EXPRESSION_CHARS] if line <= len(self.lines) else ""
            self.cache[line] = {"path": self.path, "line": line, "quote": quote}
        return self.cache[line]


class _Builder:
    def __init__(self, catalog) -> None:
        self.catalog = catalog
        self.columns: Dict[str, dict] = {}
        self.edges: Dict[tuple, dict] = {}
        self.unresolved: Dict[tuple, dict] = {}
        self.statements: List[dict] = []
        self.stars: Dict[str, List[Tuple[str, dict]]] = {}
        self.budgets: Dict[str, int] = {}  # path -> edges that file may still add
        self.read: set = set()  # column ids some edge comes from
        self.truncated = False

    def add(self, key: tuple, edge: dict) -> bool:
        """Keep a new edge unless its file's budget or the total budget is spent; False when refused."""
        if key in self.edges:
            return True
        path = edge["evidence"]["path"]
        left = self.budgets.get(path, MAX_FILE_EDGES)
        if left <= 0 or len(self.edges) >= MAX_EDGES:
            self.truncated = True
            return False
        self.budgets[path] = left - 1
        self.edges[key] = edge
        return True

    def file(self, path: str, text: str, newlines: List[int], rows) -> None:
        places = _Places(path, text, newlines)
        for item, derived, written in rows:
            target = written.table if written is not None else (
                item.statement.target.name if item.statement is not None and item.statement.target else None)
            if isinstance(written, TempWrite):
                target = written.name
            record = {"path": path, "index": item.index, "line": places.line(item.start), "target": target or None,
                      "status": item.status, "reason": item.reason}
            if derived is None or written is None:
                statement = item.statement
                if statement is not None and statement.kind == "ddl" and statement.target is not None \
                        and statement.target.name:
                    for name, display in statement.columns:
                        self.column(statement.target.name, name, display, "ddl", places.at(statement.start))
                self.statements.append(record)
                continue
            if item.statement.tail or written.misses:
                record = {**record, "status": "partial", "reason": "tail" if item.statement.tail else written.misses[0]}
            self.statements.append(record)
            if isinstance(written, TempWrite):
                for table in written.tables:
                    self.uses(places, derived, written.misses, f"table:{table}")
                continue
            if not written.table:
                target = item.statement.target
                end = target.end if target is not None and target.end > item.start else item.start
                self.unresolve(None, "dynamic", _spec(text, item.start, end), places.at(item.start))
                continue
            self.statement(text, places, derived, written)

    def statement(self, text: str, places: _Places, derived, written) -> None:
        table_id = f"table:{written.table}"
        for table in written.stars:
            self.stars.setdefault(written.table, []).append((table, places.at(derived.statement.start)))
        for name, display, output, rule in written.columns:
            to = _col_id(written.table, name)
            where = places.at(output.start)
            self.column(written.table, name, display, "sql_output" if written.defines else None, where)
            expression = _spec(text, output.start, output.end)
            if output.constant:
                fixed = written.status_cap if written.status_cap[1] else ("verified", "literal")
                if rule is not None and fixed[0] == "verified":  # matched by place or by alias only
                    fixed = ("inferred", rule)
                self.add((to, "", "constant", where["path"], where["line"]), {
                    "from": None, "to": to, "kind": "constant", "status": fixed[0], "rule": fixed[1],
                    "expression": expression, "via": [], "evidence": where})
            elif not output.leaves and output.misses and not output.misses[0].reason.startswith("table_"):
                self.missing(text, places, to, output.misses[0], expression, where)
            if output.leaves:  # a column only partly followed: each reference that reached nothing
                for miss in output.misses:
                    if not miss.reason.startswith("table_"):
                        self.missing(text, places, to, miss, expression, where)
            for leaf in output.leaves:
                if written.status_cap[1]:
                    leaf = cap(leaf, *written.status_cap)
                if rule is not None:
                    leaf = cap(leaf, "inferred", rule)
                edge = _edge(leaf, to, name, expression, where, rule)
                self.add((to, edge["from"], edge["kind"], where["path"], where["line"]), edge)
                self.reached(leaf.table, leaf.column, leaf.display, leaf.external)
        self.uses(places, derived, written.misses, table_id)

    def missing(self, text: str, places: _Places, to: str, miss: Miss, expression: str, where: dict) -> None:
        """A column's miss; one a SELECT * caused is shown at the star."""
        if miss.reason != "unknown_star" or miss.end <= miss.start:
            self.unresolve(to, miss.reason, expression, where, miss)
        else:
            self.unresolve(to, miss.reason, _spec(text, miss.start, miss.end), places.at(miss.start), miss)

    def uses(self, places: _Places, derived, misses: Tuple[str, ...], table_id: str) -> None:
        """The statement's misses, joins, filters and other reads, as edges and entries on `table_id`.
        An unknown star the derivation recorded at its own place is not recorded again."""
        stars = any(missed.miss.reason == "unknown_star" for missed in derived.missed)
        for reason in misses:
            if reason != "unknown_star" or not stars:
                self.unresolve(table_id, reason, "*", places.at(derived.statement.start))
        for usage in derived.usages:
            leaf, where = usage.leaf, places.at(usage.start)
            edge = {"from": _col_id(leaf.table, leaf.column), "to": table_id, "kind": usage.kind,
                    "status": leaf.status, "rule": leaf.rule, "via": list(leaf.via), "evidence": where}
            self.add((table_id, edge["from"], usage.kind, where["path"], where["line"]), edge)
            self.reached(leaf.table, leaf.column, leaf.display, leaf.external)
        for missed in derived.missed:
            self.unresolve(table_id, missed.miss.reason, missed.spec, places.at(missed.start), missed.miss)

    def column(self, table: str, name: str, display: str, known_from: Optional[str], defined: Optional[dict],
               external: bool = False) -> None:
        key = _col_id(table, name)
        entry = self.columns.setdefault(key, {"id": key, "table": table, "column": name, "display": display,
                                              "known_from": [], "external": external})
        if known_from and known_from not in entry["known_from"]:
            entry["known_from"] = sorted(entry["known_from"] + [known_from])
        if defined is not None and "defined" not in entry:
            entry["defined"] = defined
        if not external:
            entry["external"] = False

    def reached(self, table: str, name: str, display: str, external: bool) -> None:
        """A column some edge comes from."""
        self.column(table, name, display, None, None, external)
        self.read.add(_col_id(table, name))

    def unresolve(self, to: Optional[str], reason: str, spec: str, where: dict, miss: Optional[Miss] = None) -> None:
        entry = {"to": to, "spec": spec or "", "reason": reason, "evidence": where}
        if miss is not None and miss.candidates:
            entry["candidates"] = list(miss.candidates)
        if miss is not None and miss.source:
            entry["source"] = miss.source
        key = (where["path"], where["line"], entry["spec"], reason)
        if to is not None and to.startswith("col:"):  # one entry per column: several may share one star
            key += (to,)
        self.unresolved.setdefault(key, entry)

    def passthrough(self) -> None:
        """For T made by `SELECT * FROM S` with S's columns unknown: S.x -> T.x for each T.x read downstream."""
        pending = sorted(key for key, column in self.columns.items() if column["table"] in self.stars)
        seen = set()
        while pending:
            key = pending.pop()
            if key in seen:
                continue
            seen.add(key)
            column = self.columns[key]
            listing = self.catalog.get(column["table"])
            if listing is not None and column["column"] in listing.names and \
                    (listing.known_from != "ddl" or key not in self.read):
                continue  # the SQL that lists it has its edges; DDL says only that it exists
            for source, where in self.stars.get(column["table"], ()):
                origin = _col_id(source, column["column"])
                if not self.add((key, origin, "direct", where["path"], where["line"]), {
                        "from": origin, "to": key, "kind": "direct", "status": "inferred", "rule": "star_passthrough",
                        "expression": "*", "via": [], "evidence": where}):
                    continue
                self.reached(source, column["column"], column["display"], source not in self.catalog)
                if source in self.stars:
                    pending.append(origin)

    def documented(self) -> None:
        """Mark each column that dbt documentation lists."""
        for entry in self.columns.values():
            listing = self.catalog.get(entry["table"])
            if listing is not None and listing.known_from == "documented" and entry["column"] in listing.names \
                    and "documented" not in entry["known_from"]:
                entry["known_from"] = sorted(entry["known_from"] + ["documented"])


def document(paths: List[str], texts: Dict[str, str], newlines: Dict[str, List[int]], results, catalog,
             disagree: List[dict], digest: str, unsettled: bool) -> dict:
    builder = _Builder(catalog)
    for path in paths:
        builder.file(path, texts[path], newlines[path], results[path])
    builder.passthrough()
    builder.documented()
    return {
        "columns_version": COLUMNS_VERSION,
        "inventory_sha256": digest,
        "columns": [builder.columns[key] for key in sorted(builder.columns)],
        "edges": sorted(builder.edges.values(), key=lambda e: (e["to"], e["from"] or "", e["kind"],
                                                               e["evidence"]["path"], e["evidence"]["line"])),
        "unresolved": sorted(builder.unresolved.values(), key=lambda u: (u["evidence"]["path"], u["evidence"]["line"],
                                                                         u["spec"], u["reason"])),
        "statements": builder.statements,
        "column_sources_disagree": disagree,
        "truncated": builder.truncated or unsettled,
    }


def _edge(leaf: Leaf, to: str, name: str, expression: str, where: dict, rule: Optional[str]) -> dict:
    kind = leaf.kind
    if kind == "pass":
        kind = "direct" if leaf.column == name else "rename"
    edge = {"from": _col_id(leaf.table, leaf.column), "to": to, "kind": kind, "status": leaf.status,
            "rule": leaf.rule, "expression": expression, "via": list(leaf.via), "evidence": where}
    if leaf.condition:
        edge["role"] = "condition"
    if leaf.table_rule and leaf.table_rule != "name":
        edge["table_rule"] = leaf.table_rule
    if rule == "alias_mismatch":
        edge["alias_mismatch"] = True
    return edge


def _col_id(table: str, column: str) -> str:
    return f"col:{table}.{column}"


def _spec(text: str, start: int, end: int) -> str:
    return " ".join(text[start:end].split())[:MAX_EXPRESSION_CHARS]
