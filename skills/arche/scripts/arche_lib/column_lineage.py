"""columns.json: where each column a SQL file writes comes from, across files.

Files are parsed once. Their column lists depend on one another (a `SELECT *` from a
table another file builds), so derivation repeats until the lists stop changing."""
from __future__ import annotations

from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from . import dbt
from .column_catalog import Listing, Tables, build_catalog
from .column_document import TempWrite, document, newline_positions
from .sqlcol.ast import Statement, TableName
from .sqlcol.derive import Derived, Lookup, derive
from .sqlcol.parser import ParseError
from .sqlcol.scope import Cut, Output, QueryRelation, TableInfo, TableRelation
from .sqlcol.statements import parse
from .sqlcol.tokens import statements as split_statements

MAX_ROUNDS = 6
_UNSUPPORTED = frozenset({"pivot", "procedural", "select_into", "by_name", "multi_table_insert", "merge_star",
                          "default_values"})


class Parsed(NamedTuple):
    index: int  # the statement's place in the file, counting every statement that was read or failed
    start: int
    statement: Optional[Statement]
    status: str  # parsed | unsupported | parse_error | too_large
    reason: Optional[str]


class Written(NamedTuple):
    """One statement's output columns mapped to the table it writes."""
    table: str  # "" when the target is a run-time name
    columns: Tuple[Tuple[str, str, Output, Optional[str]], ...]  # (name, as written, output, weaker rule)
    defines: bool  # its column list is the table's (CREATE AS, a dbt model, INSERT with columns, a query)
    stars: Tuple[str, ...]  # tables a SELECT * read without knowing their columns
    status_cap: Tuple[str, str]  # (status, rule) that no edge into this table may pass; rule "" for none
    misses: Tuple[str, ...]  # reasons the statement is only partly mapped
    partial_list: bool = False  # an INSERT's column list: the table may have other columns
    open_list: bool = False  # the columns are not all known (a CREATE whose column count does not match)


Row = Tuple[Parsed, Optional[Derived], Optional[Written]]  # the third may be a TempWrite


def parse_file(text: str) -> List[Parsed]:
    found: List[Parsed] = []
    for chunk in split_statements(text):
        start = chunk.tokens[0].start if chunk.tokens else 0
        if chunk.too_large:
            found.append(Parsed(len(found), start, None, "too_large", "too_large"))
            continue
        try:
            for statement in parse(chunk.tokens):
                found.append(Parsed(len(found), statement.start, statement, "parsed", None))
        except ParseError as error:
            status = "unsupported" if error.reason in _UNSUPPORTED else "parse_error"
            found.append(Parsed(len(found), start, None, status, error.reason))
        except RecursionError:  # a last guard; the parser's own depth limit comes first
            found.append(Parsed(len(found), start, None, "parse_error", "too_deep"))
    return found


def build_columns(paths: List[str], read_path: Callable[[str], Optional[str]], extracted: Dict[str, dict],
                  projects: Dict[str, dbt.Project], models: List[dict], sources: List[dict],
                  dbt_models: List[dict], inventory_sha256: str) -> Optional[dict]:
    """The columns.json document, or None when no SQL file was analyzed."""
    sql_paths = sorted(p for p in paths if "tables" in extracted.get(p, {}))
    if not sql_paths:
        return None
    texts = {path: read_path(path) or "" for path in sql_paths}
    newlines = {path: newline_positions(texts[path]) for path in sql_paths}
    parsed = {path: parse_file(texts[path]) for path in sql_paths}
    tables = Tables(extracted, projects, models, sources, dbt_models)
    catalog: Dict[str, Listing] = dict(tables.documented)
    settled = False
    for _ in range(MAX_ROUNDS):
        results = _round(sql_paths, texts, newlines, parsed, tables, catalog)
        following, disagree = build_catalog(results, tables.documented)
        settled = following == catalog
        catalog = following
        if settled:
            break
    return document(sql_paths, texts, newlines, results, catalog, disagree, inventory_sha256, not settled)


class _Temps(dict):
    """The file's temporary tables, noting which ones a statement reads."""

    def __init__(self) -> None:
        super().__init__()
        self.read: set = set()

    def __getitem__(self, name: str) -> QueryRelation:
        self.read.add(name)
        return super().__getitem__(name)


def _round(paths: List[str], texts: Dict[str, str], newlines: Dict[str, List[int]],
           parsed: Dict[str, List[Parsed]], tables: Tables, catalog: Dict[str, Listing]) -> Dict[str, List[Row]]:
    """path -> rows for one pass over every file, with the column lists of the pass before."""
    return {path: _file_rows(path, texts[path], newlines[path], parsed[path], tables, catalog) for path in paths}


def _file_rows(path: str, text: str, newlines: List[int], items: List[Parsed], tables: Tables,
               catalog: Dict[str, Listing]) -> List[Row]:
    temps = _Temps()
    lookup = _lookup(path, tables, catalog)
    rows: List[Row] = []
    reads: List[set] = []  # per row, the temporary tables it read
    first_query = True  # a model's query is its first query, or its first statement that failed to parse
    for item in items:
        is_main = first_query and (item.statement is None or item.statement.kind == "query")
        first_query = first_query and not is_main
        reads.append(set())
        if item.statement is None:
            rows.append((item, None, None))
            continue
        temps.read = reads[-1]
        try:
            derived = derive(text, item.statement, lookup, temps, newlines)
        except RecursionError:  # a last guard; the derivation's own depth limit comes first
            rows.append((item._replace(statement=None, status="parse_error", reason="too_deep"), None, None))
            continue
        written = _written(path, item, derived, lookup, tables.model_tables.get(path) if is_main else None)
        if written is not None and item.statement.temp and item.statement.target.name:
            name = item.statement.target.name
            temps[name] = QueryRelation(
                f"temp:{name}",
                [output._replace(name=name, display=display) for name, display, output, _ in written.columns],
                list(derived.stars), derived.cut if item.statement.columns is None else None)
            # A temporary table is not a node; later statements pass through it.
            written = TempWrite(name, written.misses, ())
        rows.append((item, derived, written))
    return _temp_readers(rows, reads)


def _temp_readers(rows: List[Row], reads: List[set]) -> List[Row]:
    """Each temporary table's uses go to the tables written by the later statements that read it."""
    readers: Dict[str, set] = {}  # temp name -> tables
    done: List[Row] = list(rows)
    for index in range(len(rows) - 1, -1, -1):
        item, derived, written = rows[index]
        if isinstance(written, TempWrite):
            found = readers.pop(written.name, set())  # a later CREATE of the same name starts over
            written = written._replace(tables=tuple(sorted(found)) or (f"temp:{written.name}",))
            done[index] = (item, derived, written)
            into = set(written.tables)
        elif written is not None and written.table:
            into = {written.table}
        else:
            continue
        for name in reads[index]:
            readers.setdefault(name, set()).update(into)
    return done


def _lookup(path: str, tables: Tables, catalog: Dict[str, Listing]) -> Lookup:
    cache: Dict[TableName, TableInfo] = {}

    def lookup(table: TableName) -> TableInfo:
        key = table._replace(start=0, end=0)
        if key not in cache:
            name, status, rule, reason, candidates = tables.resolve(path, table)
            listing = catalog.get(name) if name else None
            cache[key] = TableInfo(name, status, rule, listing.names if listing else None,
                                   bool(listing) and listing.verifiable, bool(listing) and listing.open,
                                   reason, candidates, listed_from=listing.known_from if listing else None)
        return cache[key]

    return lookup


def _star_tables(stars) -> Tuple[str, ...]:
    """Tables behind a SELECT * whose columns are unknown, through CTEs and derived tables."""
    found = set()
    pending = list(stars)
    while pending:
        relation = pending.pop()
        if isinstance(relation, TableRelation) and relation.info.table:
            found.add(relation.info.table)
        elif isinstance(relation, QueryRelation):
            pending.extend(relation.stars)
    return tuple(sorted(found))


def _written(path: str, item: Parsed, derived: Derived, lookup: Lookup,
             model: Optional[Tuple[str, bool]]) -> Optional[Written]:
    """The statement's outputs as columns of the table it writes; None for DDL. Set operation branches
    whose widths differ leave the statement partial."""
    written = _write(path, item, derived, lookup, model)
    if written is not None and written.table and "arity" not in written.misses and \
            any(missed.miss.reason == "arity" for missed in derived.missed):
        written = written._replace(misses=written.misses + ("arity",))
    return written


def _write(path: str, item: Parsed, derived: Derived, lookup: Lookup,
           model: Optional[Tuple[str, bool]]) -> Optional[Written]:
    statement = item.statement
    outputs = list(derived.outputs)
    stars = _star_tables(derived.stars)
    if statement.kind == "ddl":
        return None
    if statement.kind == "query":
        cap = ("verified", "")
        if model is None:
            table = f"query:{path}#{item.index}"
        else:
            table = model[0]
            cap = ("inferred", "dynamic_model") if model[1] else cap
        return Written(table, _pair(_output_names(outputs), outputs), True, stars, cap, ())
    target = statement.target
    if target is None or target.name is None or target.dynamic or target.dbt is not None:
        return Written("", (), False, (), ("verified", ""), ("dynamic",))
    constants = tuple((c.column, c.display, Output(c.column, c.display, (), (), c.expr.start, c.expr.end,
                                                   not c.expr.refs and not c.expr.calls, c.expr.dynamic), None)
                      for c in statement.constants)
    if statement.kind == "merge":
        merged = _merge_outputs(outputs)  # UPDATE SET c and INSERT (c) are two branches of one column
        return Written(target.name, _pair([(o.name, o.display, None) for o in merged], merged), False, (),
                       ("verified", ""), ())
    if statement.kind == "create":
        if statement.columns is None:
            return Written(target.name, _pair(_output_names(outputs), outputs) + constants, True, stars,
                           ("verified", ""), ())
        names = [(name, display, None) for name, display in statement.columns]
        if derived.cut is not None:
            return _by_place(target.name, names, outputs, derived.cut, stars, constants, True, False)
        if len(statement.columns) == len(outputs) or derived.stars:
            return Written(target.name, _pair(names, outputs) + constants, True, stars, ("verified", ""), ())
        return Written(target.name, constants, True, stars, ("verified", ""), ("arity",), open_list=True)
    return _insert(target.name, statement, outputs, derived, stars, constants, lookup)


def _by_place(table: str, names: list, outputs: List[Output], cut: Cut, stars: Tuple[str, ...], constants: tuple,
              defines: bool, listed: bool, rule: Optional[str] = None) -> Written:
    """Columns matched by place against outputs with an unknown SELECT * at `cut.at`: those before it
    are mapped; a listed name after it is a column with no known source; a table's own columns after
    it (no list) are left out."""
    if len(names) < len(outputs):
        return Written(table, constants, defines, stars, ("verified", ""), ("arity",), listed, open_list=True)
    known = [(name, display, _mismatch(rule, name, output)) for (name, display, _), output
             in zip(names[:cut.at], outputs[:cut.at])]
    pairs = tuple(pair for pair in _pair(known, outputs) if pair[0])
    rest = names[cut.at:]
    if listed or defines:
        pairs += tuple((name, display, Output(name, display, (), (cut.miss,), cut.start, cut.end, False, False), None)
                       for name, display, _ in rest)
    return Written(table, pairs + constants, defines, stars, ("verified", ""), ("unknown_star",) if rest else (),
                   listed)


def _mismatch(rule: Optional[str], name: str, output: Output) -> Optional[str]:
    """A positional match whose SELECT alias names another column is only inferred."""
    return "alias_mismatch" if rule == "positional" and output.name is not None and output.name != name else rule


def _insert(table: str, statement: Statement, outputs: List[Output], derived: Derived, stars: Tuple[str, ...],
            constants: tuple, lookup: Lookup) -> Written:
    """INSERT: by its column list, else by position against the table's columns, else by SELECT alias."""
    misses: List[str] = []
    listed = statement.columns is not None
    if listed:
        expected = list(statement.columns) + list(statement.partitions)
        rule, defines = None, True
    else:
        info = lookup(TableName(table, False, None, 0))
        static = {c.column for c in statement.constants}
        if info.names is not None and not info.open and info.listed_from != "insert_alias":
            expected = [(name, name) for name in info.names if name not in static]
            rule, defines = "positional", False
        else:
            expected = [(o.name or "", o.display or "") for o in outputs]
            if any(not name for name, _ in expected):
                misses.append("unnamed")
            rule, defines = "select_alias", False
    if derived.cut is not None and rule != "select_alias":
        return _by_place(table, [(name, display, rule) for name, display in expected], outputs, derived.cut, stars,
                         constants, False, listed, rule)
    if derived.stars and rule == "positional":
        return Written(table, constants, defines, stars, ("verified", ""), ("unknown_star",), listed)
    if not derived.stars and len(expected) != len(outputs):
        return Written(table, constants, defines, stars, ("verified", ""), ("arity",), listed)
    names = [(name, display, _mismatch(rule, name, output)) for (name, display), output in zip(expected, outputs)]
    pairs = tuple(pair for pair in _pair(names, outputs) if pair[0])
    return Written(table, pairs + constants, defines, stars, ("verified", ""), tuple(misses), listed)


def _merge_outputs(outputs: List[Output]) -> List[Output]:
    """One output per name, with the leaves of every assignment to it."""
    merged: Dict[str, Output] = {}
    for output in outputs:
        if output.name in merged:
            first = merged[output.name]
            merged[output.name] = first._replace(leaves=first.leaves + output.leaves,
                                                 misses=first.misses + output.misses,
                                                 constant=first.constant and output.constant)
        else:
            merged[output.name] = output
    return list(merged.values())


def _output_names(outputs: List[Output]) -> List[Tuple[str, str, None]]:
    """Output names; an expression without one is `_c<position>`."""
    return [(o.name or f"_c{i}", o.display or f"_c{i}", None) for i, o in enumerate(outputs)]


def _pair(names, outputs) -> Tuple[Tuple[str, str, Output, Optional[str]], ...]:
    """(name, as written, output, weaker rule); a name given twice ({% if %} branches) is only inferred."""
    counts: Dict[str, int] = {}
    for name, _, _ in names:
        counts[name] = counts.get(name, 0) + 1
    return tuple((name, display, output, "duplicate_name" if counts[name] > 1 and rule is None else rule)
                 for (name, display, rule), output in zip(names, outputs))
