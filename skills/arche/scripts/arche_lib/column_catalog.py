"""Which table a read names, and which columns each table has, for column lineage.

Table names are matched as the table lineage matches them (`lineage.resolve_matched`,
`dbt.resolve_ref`, `dbt.resolve_source`), so a column always hangs on the same table name
graph.json uses. Column lists come from SQL that defines a table, then DDL, then the column
lists of INSERTs (open: other columns may exist), then dbt documentation, then the aliases of
an INSERT without a column list."""
from __future__ import annotations

from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

from . import dbt, lineage
from .sqlcol.ast import TableName

Resolved = Tuple[Optional[str], str, Optional[str], Optional[str], Tuple[str, ...]]
_PRIORITY = ("sql_output", "ddl", "insert_list", "documented", "insert_alias")


class Listing(NamedTuple):
    """A table's columns as the repository says them, in order."""
    names: Tuple[str, ...]
    known_from: str  # sql_output | ddl | insert_list | documented | insert_alias
    open: bool  # more columns may exist (documentation, or a SELECT * from an unknown source)
    displays: Tuple[str, ...]
    where: Tuple[str, int] = ("", 0)  # (path, statement index) of the SQL or DDL that gave it

    @property
    def verifiable(self) -> bool:
        """True when the names come from SQL output or DDL in the repository and are complete."""
        return self.known_from in ("sql_output", "ddl") and not self.open


class Tables:
    """Everything a lookup needs that does not change between rounds."""

    def __init__(self, extracted: Dict[str, dict], projects: Dict[str, dbt.Project], models: List[dict],
                 sources: List[dict], dbt_models: List[dict]) -> None:
        self.extracted, self.projects = extracted, projects
        # A model whose alias is not a literal has no table; its file name stands in (spec Q10).
        self.model_tables = {m["file"]: (m["table"] or m["model"], m["table"] is None) for m in models}
        paths = sorted(p for p in extracted if "tables" in extracted[p])
        writes = [(p, item["table"]) for p in paths for item in extracted[p]["tables"]
                  if item["access"] == "write" and not item["temp"] and not item["dynamic"]]
        model_writes = [(m["file"], m["table"]) for m in models if m["table"]]
        self.writers = lineage.index(writes + model_writes)
        self.plain = lineage.index(writes) if model_writes else self.writers  # source() never names a model
        self.documented: Dict[str, Listing] = {}
        for model in dbt_models:
            if model["table"] and model["columns"]:
                self._document(model["table"], [column["name"] for column in model["columns"]])
        for source in sources:
            if source.get("status") == "resolved" and source["columns"]:
                self._document(source["resolved"], [column["name"] for column in source["columns"]])

    def _document(self, table: str, names: List[str]) -> None:
        if table not in self.documented and names and all(names):
            # Documentation may leave columns out, so the list is open.
            self.documented[table] = Listing(_unique(n.lower() for n in names), "documented", True,
                                             tuple(names))

    def resolve(self, path: str, table: TableName) -> Resolved:
        """(node table, status, rule, reason, candidates) for a table that `path` reads."""
        if table.dbt is not None:
            return self._dbt(path, table)
        if table.dynamic:
            return None, "unresolved", None, "dynamic", ()
        if table.name is None:
            return None, "unresolved", None, "unresolved_table", ()
        return _first(lineage.resolve_matched(table.name, False, self.writers), table.name)

    def _dbt(self, path: str, table: TableName) -> Resolved:
        kind, args, version = table.dbt
        if args is None:
            return None, "unresolved", None, "dynamic", ()
        if kind == "source":
            found = dbt.resolve_source(list(args), path, self.projects) if len(args) == 2 else None
            if found is not None and found["status"] == "ambiguous":
                return None, "unresolved", None, "ambiguous", ()
            name = found["table"] if found is not None else ".".join(args).lower()
            if "{}" in name.rsplit(".", 1)[-1]:
                return None, "unresolved", None, "dynamic", ()
            return _first(lineage.resolve_matched(name, False, self.plain), name)
        ref = {"ref": args[-1] if args else "", "package": args[0] if len(args) == 2 else None,
               "dynamic": not 1 <= len(args) <= 2, "quote": "", "line": 0,
               **({"version": version} if version else {})}
        result = dbt.resolve_ref(ref, path, self.projects, self.extracted)
        if result["status"] == "external":
            return f"{result['package']}.{ref['ref']}", "external", None, None, ()
        if result["status"] != "verified" or result["to"] not in self.model_tables:
            return None, "unresolved", None, result.get("reason", "not_found"), tuple(result.get("candidates", ()))
        name, dynamic = self.model_tables[result["to"]]
        return name, "inferred" if dynamic else "verified", "dynamic_model" if dynamic else "dbt_ref", None, ()


def _first(results: List[dict], name: str) -> Resolved:
    """Every result of a match names the same table, so the first one says it."""
    first = results[0]
    if first["status"] == "external":
        return name, "external", None, None, ()
    if first["status"] == "unresolved":
        return None, "unresolved", None, first["reason"], tuple(first.get("candidates", ()))
    return first["matched"], first["status"], first["rule"], None, ()


def build_catalog(results: Dict[str, list], documented: Dict[str, Listing]
                  ) -> Tuple[Dict[str, Listing], List[dict]]:
    """(each table's chosen column list, tables whose lists disagree).

    `results` maps a path to its rows of (Parsed, Derived or None, Written or None).
    A list from dbt documentation is not compared: it may leave columns out by design."""
    found: Dict[str, Dict[str, Listing]] = {}
    for path in sorted(results):
        for item, _, written in results[path]:
            statement = item.statement
            if statement is not None and statement.kind == "ddl" and statement.target and statement.target.name:
                _offer(found, statement.target.name, Listing(
                    _unique(n for n, _ in statement.columns), "ddl", False,
                    tuple(d for _, d in statement.columns), (path, item.index)))
            if written is None or not written.table or written.table.startswith("query:"):
                continue
            names = _unique(column[0] for column in written.columns)
            displays = {column[0]: column[1] for column in reversed(written.columns)}
            listing = Listing(names, _kind(written), bool(written.stars) or written.partial_list,
                              tuple(displays[n] for n in names), (path, item.index))
            if listing.known_from == "insert_list" and "insert_list" in found.get(written.table, {}):
                listing = _merge(found[written.table]["insert_list"], listing)
                found[written.table]["insert_list"] = listing
            _offer(found, written.table, listing)
    for table, listing in documented.items():
        _offer(found, table, listing)
    catalog: Dict[str, Listing] = {}
    disagree: List[dict] = []
    for table in sorted(found):
        offered = found[table]
        catalog[table] = next(offered[kind] for kind in _PRIORITY if kind in offered)
        if "sql_output" in offered and "ddl" in offered and \
                set(offered["sql_output"].names) != set(offered["ddl"].names) and not offered["sql_output"].open:
            disagree.append(_difference(table, offered["sql_output"], offered["ddl"]))
    return catalog, disagree


def _kind(written) -> str:
    if written.partial_list:
        return "insert_list"
    return "sql_output" if written.defines else "insert_alias"


def _merge(first: Listing, later: Listing) -> Listing:
    """Several INSERT column lists into one table: one open list, names in the order first seen."""
    names = _unique(first.names + later.names)
    displays = dict(zip(later.names, later.displays))
    displays.update(zip(first.names, first.displays))
    return first._replace(names=names, displays=tuple(displays[n] for n in names))


def _offer(found: Dict[str, Dict[str, Listing]], table: str, listing: Listing) -> None:
    """The first list of each kind, by path and statement order, is the one kept."""
    found.setdefault(table, {}).setdefault(listing.known_from, listing)


def _difference(table: str, chosen: Listing, other: Listing) -> dict:
    return {"table": table, "chosen": chosen.known_from, "other": other.known_from,
            "only_chosen": sorted(set(chosen.names) - set(other.names)),
            "only_other": sorted(set(other.names) - set(chosen.names)),
            "chosen_at": {"path": chosen.where[0], "index": chosen.where[1]},
            "other_at": {"path": other.where[0], "index": other.where[1]}}


def _unique(names: Iterable[str]) -> Tuple[str, ...]:
    seen: Dict[str, None] = {}
    for name in names:
        seen.setdefault(name, None)
    return tuple(seen)
