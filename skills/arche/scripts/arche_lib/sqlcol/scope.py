"""Relations a SELECT can see and how a column reference finds its source.

A relation answers `column(name, how)` with the leaf table columns behind that name.
Leaves always name real tables: CTEs, derived tables and temporary tables of the same
file are passed through and recorded in `via`."""
from __future__ import annotations

from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

STATUS_RANK = {"verified": 2, "inferred": 1, "unresolved": 0}
KIND_RANK = {"pass": 0, "transform": 1, "window": 2, "aggregate": 3}


class Leaf(NamedTuple):
    table: str
    column: str
    display: str
    kind: str  # pass | transform | window | aggregate
    condition: bool
    status: str  # verified | inferred
    rule: str
    via: Tuple[str, ...]
    external: bool
    table_rule: Optional[str]


class Miss(NamedTuple):
    """A reference that reaches no table column."""
    reason: str  # ambiguous | unknown_star | dynamic | unknown_column | unknown_qualifier | table_<reason>
    candidates: Tuple[str, ...] = ()
    source: Optional[str] = None
    start: int = -1  # where the table that could not be matched is read


class TableInfo(NamedTuple):
    """What the catalog knows about one table a file reads."""
    table: Optional[str]  # the table's node name; None when the name cannot be matched
    status: str  # verified | inferred | external | unresolved
    rule: Optional[str]
    names: Optional[Tuple[str, ...]]  # its columns in order, None when unknown
    verifiable: bool  # the names come from SQL output or DDL in the repository
    open: bool  # the names are only those known; a SELECT * from an unknown source made it
    reason: Optional[str] = None
    candidates: Tuple[str, ...] = ()
    spec: Optional[str] = None  # the table as written, for reports
    start: int = -1
    listed_from: Optional[str] = None  # where `names` came from: sql_output | ddl | documented | insert_alias


Result = Tuple[List[Leaf], Optional[Miss]]


def weaker(status: str, other: str) -> str:
    return status if STATUS_RANK[status] <= STATUS_RANK[other] else other


def cap(leaf: Leaf, status: str, rule: str) -> Leaf:
    """The leaf with its status no higher than `status`; `rule` names the weaker step."""
    if STATUS_RANK[leaf.status] <= STATUS_RANK[status]:
        return leaf
    return leaf._replace(status=status, rule=rule)


def dedupe(leaves: List[Leaf]) -> List[Leaf]:
    """One leaf per (table, column, kind, role): the most certain one, first by order."""
    best: Dict[tuple, "Leaf"] = {}
    for leaf in leaves:
        key = (leaf.table, leaf.column, leaf.kind, leaf.condition)
        if key not in best or STATUS_RANK[leaf.status] > STATUS_RANK[best[key].status]:
            best[key] = leaf
    return list(best.values())


Source = Tuple[str, "Relation", Optional[str]]  # (alias, relation, full table name or None)


def named(sources: List[Source], qualifier: str) -> List[Source]:
    """The sources a qualifier names. An alias written in FROM wins; a table that has such an alias
    is not named by its table name; a table without one is named by its full name or a "."-suffix."""
    by_alias, by_name = [], []
    for source in sources:
        alias, _, full = source
        explicit = full is None or alias != full.rsplit(".", 1)[-1]
        if explicit:
            if alias and alias == qualifier:
                by_alias.append(source)
        elif full == qualifier or full.endswith("." + qualifier):
            by_name.append(source)
    return by_alias or by_name


class Relation:
    """Columns a source offers. `has` is True, False, or None when the list is unknown."""

    label = ""

    def has(self, name: str) -> Optional[bool]:
        raise NotImplementedError

    def column(self, name: str, determined: bool, rule: str = "qualified") -> Result:
        raise NotImplementedError

    def names(self) -> Optional[Tuple[str, ...]]:
        raise NotImplementedError


class TableRelation(Relation):
    def __init__(self, info: TableInfo) -> None:
        self.info = info

    def has(self, name: str) -> Optional[bool]:
        if self.info.names is None:
            return None
        if name in self.info.names:
            return True
        return None if self.info.open else False

    def names(self) -> Optional[Tuple[str, ...]]:
        return None if self.info.names is None or self.info.open else self.info.names

    def column(self, name: str, determined: bool, rule: str = "qualified") -> Result:
        """`determined`: a qualifier (rule qualified) or a column list (rule listed) picked this table."""
        info = self.info
        if info.table is None:
            return [], Miss("table_" + (info.reason or "unresolved"), info.candidates, info.spec, info.start)
        listed = info.names is not None and name in info.names
        if not determined:
            status, rule = "inferred", rule if rule != "qualified" else "single_source"
        elif info.status == "external" and not listed:
            status = "verified"  # the name is the one written; nothing to match
        elif not listed:
            status, rule = "inferred", "unlisted_column"
        elif not info.verifiable:
            status = "inferred"
            rule = info.listed_from if info.listed_from in ("documented", "insert_alias") else "open_list"
        else:
            status = "verified"
        if info.status == "inferred" and status == "verified":
            status, rule = "inferred", info.rule or "table_name"
        return [Leaf(info.table, name, name, "pass", False, status, rule, (), info.status == "external",
                     info.rule)], None


class Output(NamedTuple):
    """One output column of a query, with the leaves behind it."""
    name: Optional[str]
    display: Optional[str]
    leaves: Tuple[Leaf, ...]
    misses: Tuple[Miss, ...]
    start: int  # the item's place
    end: int
    constant: bool  # no column and no run-time value made it
    dynamic: bool


class Cut(NamedTuple):
    """Where the first SELECT * whose columns are not all known starts in an output list: the outputs
    before `at` have known places; from `at` on, the star may add any number of columns."""
    at: int
    miss: Miss  # recorded for a column matched by place at or after `at`
    start: int  # the star's place
    end: int


class QueryRelation(Relation):
    """A CTE, a derived table or a temporary table of the same file."""

    def __init__(self, label: str, outputs: List[Output], stars: List[Relation], cut: Optional[Cut] = None) -> None:
        self.label, self.outputs, self.stars, self.cut = label, outputs, stars, cut
        self.by_name: Dict[str, List[Output]] = {}
        for output in outputs:
            if output.name is not None:
                self.by_name.setdefault(output.name, []).append(output)

    def has(self, name: str) -> Optional[bool]:
        if name in self.by_name:
            return True
        return None if self.stars else False

    def names(self) -> Optional[Tuple[str, ...]]:
        return None if self.stars else tuple(o.name for o in self.outputs if o.name is not None)

    def column(self, name: str, determined: bool, rule: str = "qualified") -> Result:
        found = self.by_name.get(name)
        if found is None:
            if len(self.stars) == 1:
                leaves, miss = self.stars[0].column(name, determined)
                return [cap(leaf, "inferred", "star_passthrough")._replace(via=(self.label,) + leaf.via)
                        for leaf in leaves], miss
            if self.stars:
                return [], Miss("ambiguous", tuple(sorted(s.label for s in self.stars)))
            return [], Miss("unknown_column")
        leaves: List[Leaf] = []
        misses: List[Miss] = []
        for output in found:
            for leaf in output.leaves:
                leaf = leaf._replace(via=(self.label,) + leaf.via)
                leaves.append(cap(leaf, "inferred", "duplicate_name") if len(found) > 1 else leaf)
            misses.extend(output.misses)
        leaves = dedupe(leaves)
        if not determined:
            leaves = [cap(leaf, "inferred", rule if rule != "qualified" else "single_source") for leaf in leaves]
        return leaves, misses[0] if misses and not leaves else None


class FunctionRelation(Relation):
    """UNNEST, LATERAL VIEW and other table functions: every column comes from the inputs."""

    def __init__(self, label: str, columns: Tuple[str, ...], inputs: Callable[[], Result]) -> None:
        self.label, self.columns, self.inputs = label, columns, inputs

    def has(self, name: str) -> Optional[bool]:
        return (name in self.columns) if self.columns else None

    def names(self) -> Optional[Tuple[str, ...]]:
        return self.columns or None

    def column(self, name: str, determined: bool, rule: str = "qualified") -> Result:
        leaves, miss = self.inputs()
        return [leaf._replace(kind="transform" if KIND_RANK[leaf.kind] < 1 else leaf.kind, status="inferred",
                              rule="table_function", via=(self.label,) + leaf.via)
                for leaf in leaves], miss


class Scope:
    """The sources one SELECT sees, in FROM order, and the scope around it."""

    def __init__(self, sources: List[Tuple[str, Relation, Optional[str]]], using: Tuple[str, ...],
                 parent: Optional["Scope"]) -> None:
        self.sources = sources  # (alias, relation, full table name or None)
        self.using = using
        self.parent = parent

    def resolve(self, parts: Tuple[str, ...]) -> Result:
        if len(parts) > 1:
            for end in range(len(parts), 1, -1):  # o.amt.sub: the longest qualifier naming a source
                found = self._qualified(parts[:end])
                if found is not None:
                    if end == len(parts):
                        return found
                    return [cap(leaf, "inferred", "struct_field") for leaf in found[0]], found[1]
            leaves, miss = self.resolve(parts[:1])  # a struct field: a.b where a is a column
            if leaves:
                return [cap(leaf, "inferred", "struct_field") for leaf in leaves], None
            return [], Miss("unknown_qualifier")
        return self._unqualified(parts[0])

    def _qualified(self, parts: Tuple[str, ...]) -> Optional[Result]:
        qualifier, name = ".".join(parts[:-1]), parts[-1]
        scope: Optional[Scope] = self
        while scope is not None:
            found = named(scope.sources, qualifier)
            if len(found) == 1:
                return found[0][1].column(name, True)
            if found:
                return [], Miss("ambiguous", tuple(sorted(_source_name(s) for s in found)))
            scope = scope.parent
        return None

    def _unqualified(self, name: str) -> Result:
        scope: Optional[Scope] = self
        while scope is not None:
            known = [s for s in scope.sources if s[1].has(name) is True]
            open_ = [s for s in scope.sources if s[1].has(name) is None]
            if name in scope.using:
                leaves: List[Leaf] = []
                for _, relation, _ in known + open_:
                    leaves.extend(cap(leaf, "inferred", "using") for leaf in relation.column(name, True)[0])
                return leaves, None
            if len(known) == 1:
                leaves, miss = known[0][1].column(name, not open_, "known_source" if open_ else "listed")
                return ([cap(leaf, "inferred", "known_source") for leaf in leaves] if open_ else leaves), miss
            if len(known) > 1:
                return [], Miss("ambiguous", tuple(sorted(_source_name(s) for s in known)))
            if len(open_) == 1:
                rule = "single_source" if len(scope.sources) == 1 else "only_open_source"
                leaves, miss = open_[0][1].column(name, False, rule)
                return [cap(leaf, "inferred", rule) for leaf in leaves], miss
            if open_:
                return [], Miss("ambiguous", tuple(sorted(_source_name(s) for s in open_)))
            scope = scope.parent
        return [], Miss("unknown_column")


def _source_name(source: Tuple[str, Relation, Optional[str]]) -> str:
    return source[2] or source[1].label or source[0]
