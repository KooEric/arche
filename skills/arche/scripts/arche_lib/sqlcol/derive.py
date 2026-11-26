"""Output columns of each statement in one file, traced to the table columns behind them,
and the columns the statement uses without passing them on (filters, join and group keys)."""
from __future__ import annotations

from bisect import bisect_left
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from . import MAX_DEPTH, MAX_SELECT_ITEMS
from .ast import AGG, CONDITION, ORDER, WINDOW, Expr, Query, QuerySource, Select, SetOp, Statement, \
    TableName, TableSource, Values
from .scope import KIND_RANK, Cut, FunctionRelation, Leaf, Miss, Output, QueryRelation, Relation, Result, Scope, \
    TableInfo, TableRelation, cap, dedupe, named

Lookup = Callable[[TableName], TableInfo]
Body = Tuple[List[Output], List[Relation], Optional[Cut]]  # outputs, unknown star sources, first unknown star


class Usage(NamedTuple):
    leaf: Leaf
    kind: str  # filter | join_key | group_key | order_key
    start: int


class Missed(NamedTuple):
    miss: Miss
    spec: str
    start: int


class Derived(NamedTuple):
    """One statement: what it writes and where each written column comes from."""
    statement: Statement
    outputs: Tuple[Output, ...]
    usages: Tuple[Usage, ...]
    missed: Tuple[Missed, ...]
    stars: Tuple[Relation, ...]  # sources of a SELECT * whose columns are unknown
    cut: Optional[Cut] = None  # where the first such SELECT * starts among the outputs


class _Env:
    """CTEs in scope: computed the first time something reads them."""

    def __init__(self, parent: Optional["_Env"]) -> None:
        self.parent, self.entries = parent, {}  # type: Optional[_Env], Dict[str, list]

    def find(self, name: str) -> Optional[list]:
        env: Optional[_Env] = self
        while env is not None:
            if name in env.entries:
                return env.entries[name]
            env = env.parent
        return None


class _TooDeep(Relation):
    """A source past the depth limit: its columns are unknown, and reading one reports the limit once."""

    def __init__(self, label: str, spec: str, start: int) -> None:
        self.label, self.miss = label, Miss("too_deep", (), spec, start)

    def has(self, name: str) -> Optional[bool]:
        return None

    def names(self) -> Optional[Tuple[str, ...]]:
        return None

    def column(self, name: str, determined: bool, rule: str = "qualified") -> Result:
        return [], self.miss


class Deriver:
    def __init__(self, text: str, lookup: Lookup, temps: Dict[str, QueryRelation], newlines: List[int]) -> None:
        self.text, self.lookup, self.temps, self.newlines = text, lookup, temps, newlines
        self.usages: List[Usage] = []
        self.missed: List[Missed] = []
        self.reported: set = set()  # (reason, start) of each unmatched table or depth limit already reported
        self.reading: Dict[int, bool] = {}  # recursive CTEs whose later branches are being read: read itself?
        self.filled: List[list] = []  # CTE entries in the order their relation was settled
        self.depth = 0

    def line(self, position: int) -> int:
        return bisect_left(self.newlines, position) + 1

    def spec(self, start: int, end: int) -> str:
        return " ".join(self.text[start:end].split())[:200]

    # -- queries ---------------------------------------------------------------------
    def query(self, query: Query, scope: Optional[Scope], env: Optional[_Env], label: str) -> QueryRelation:
        if query.ctes:
            env = _Env(env)
            for cte in query.ctes:
                env.entries[cte.name] = [cte, None, env]
        if isinstance(query.body, Select):  # ORDER BY after a SELECT sees its sources
            outputs, stars, cut = self.select(query.body, scope, env, query.orders)
        else:  # after a set operation ORDER BY names output columns
            outputs, stars, cut = self.body(query.body, scope, env)
            self.uses(query.orders, Scope([], (), scope), env, "order_key", outputs, _width(outputs, cut))
        return QueryRelation(label, outputs, stars, cut)

    def body(self, body, scope: Optional[Scope], env: Optional[_Env]) -> Body:
        if isinstance(body, Query):
            relation = self.query(body, scope, env, "")
            return relation.outputs, relation.stars, relation.cut
        if isinstance(body, Select):
            return self.select(body, scope, env)
        if isinstance(body, Values):
            return self.values(body, scope, env), [], None
        return self.set_op(body, scope, env)

    def set_op(self, body: SetOp, scope: Optional[Scope], env: Optional[_Env]) -> Body:
        return self.merge([self.body(branch, scope, env) for branch in body.branches], body.start)

    def merge(self, branches: List[Body], start: int) -> Body:
        """Branches line up by place, and only before the first unknown SELECT * of any of them.
        A branch whose width cannot match the first one's is left out and reported once."""
        first, _, first_cut = branches[0]
        stars = [star for _, branch_stars, _ in branches for star in branch_stars]
        kept = [branch for branch in branches[1:] if _fits(first, first_cut, branch[0], branch[2])]
        if len(kept) < len(branches) - 1:
            self.missed.append(Missed(Miss("arity"), "*", start))
        cuts = [cut for cut in [first_cut] + [branch[2] for branch in kept] if cut is not None]
        cut = min(cuts, key=lambda found: found.at) if cuts else None
        merged = []
        for index, output in enumerate(first):
            if cut is not None and index >= cut.at:  # its place in the other branches is unknown
                merged.append(output._replace(misses=output.misses + (cut.miss,)))
                continue
            leaves, misses, constant, dynamic = list(output.leaves), list(output.misses), output.constant, output.dynamic
            for outputs, _, _ in kept:
                other = outputs[index]
                leaves.extend(other.leaves)
                misses.extend(other.misses)
                constant, dynamic = constant or other.constant, dynamic or other.dynamic
            merged.append(output._replace(leaves=tuple(dedupe(leaves)), misses=tuple(misses), constant=constant,
                                          dynamic=dynamic))
        return merged, stars, cut

    def values(self, body: Values, scope: Optional[Scope], env: Optional[_Env]) -> List[Output]:
        width = max(len(row) for row in body.rows)
        outputs = []
        for index in range(width):
            leaves: List[Leaf] = []
            misses: List[Miss] = []
            dynamic = False
            for row in body.rows:
                if index < len(row):
                    found, missed = self.value(row[index], Scope([], (), scope), env)
                    leaves.extend(found)
                    misses.extend(missed)
                    dynamic = dynamic or row[index].dynamic
            outputs.append(Output(None, None, tuple(leaves), tuple(misses), body.start, body.start, not leaves,
                                  dynamic))
        return outputs

    def select(self, select: Select, parent: Optional[Scope], env: Optional[_Env], orders: Tuple[Expr, ...] = ()
               ) -> Body:
        scope = Scope([], tuple(name for names in select.using for name in names), parent)
        for source in select.sources:
            scope.sources.append(self.source(source, scope, parent, env))
        outputs: List[Output] = []
        stars: List[Relation] = []
        cut: Optional[Cut] = None
        for item in select.items:
            if len(outputs) >= MAX_SELECT_ITEMS:
                self.missed.append(Missed(Miss("too_many_columns"), "*", item.start))
                break
            if item.star is not None:
                expanded, found = self.expand(item.star, scope, env, stars, MAX_SELECT_ITEMS - len(outputs))
                if cut is None and found is not None:
                    cut = found._replace(at=len(outputs) + found.at)
                outputs.extend(expanded)
                continue
            leaves, misses = self.value(item.expr, scope, env)
            leaves = dedupe(leaves)
            expr = item.expr
            constant = not expr.refs and not expr.subqueries and not expr.dynamic and not expr.calls
            if expr.dynamic and not leaves:
                misses = [Miss("dynamic")] + misses  # a macro or variable made it; passed on through SELECT *
            outputs.append(Output(item.name, item.display, tuple(leaves), tuple(misses), item.start, item.end,
                                  constant, item.expr.dynamic))
        width = _width(outputs, cut)
        self.uses(select.joins, scope, env, "join_key", outputs, width)
        for names in select.using:
            for name in names:
                for _, relation, _ in scope.sources:
                    if relation.has(name) is not False:
                        for leaf in relation.column(name, True)[0]:
                            self.usages.append(Usage(leaf, "join_key", select.start))
        self.uses(select.where, scope, env, "filter", outputs, width)
        self.uses(select.groups, scope, env, "group_key", outputs, width)
        self.uses(select.orders + orders, scope, env, "order_key", outputs, width)
        for _, relation, _ in scope.sources:  # a table function no column read still records its inputs
            if isinstance(relation, FunctionRelation):
                relation.inputs()
        return outputs, stars, cut

    def source(self, source, scope: Scope, parent: Optional[Scope], env: Optional[_Env]
               ) -> Tuple[str, Relation, Optional[str]]:
        if isinstance(source, TableSource):  # a CTE it names counts its own level in cte()
            return self._source(source, scope, parent, env)
        if self.depth >= MAX_DEPTH:
            return source.alias or "", self.too_deep("", source.start), None
        self.depth += 1
        try:
            return self._source(source, scope, parent, env)
        finally:
            self.depth -= 1

    def _source(self, source, scope: Scope, parent: Optional[Scope], env: Optional[_Env]
                ) -> Tuple[str, Relation, Optional[str]]:
        if isinstance(source, TableSource):
            return source.alias, self.table(source.table, env), source.table.name
        if isinstance(source, QuerySource):
            label = f"subquery:{self.line(source.start)}"
            relation = self.query(source.query, parent, env, label)
            if source.columns:
                relation = _renamed(label, (relation.outputs, relation.stars, relation.cut), source.columns)
            return source.alias or "", relation, None
        label = f"function:{self.line(source.start)}"
        before = Scope(list(scope.sources), scope.using, scope.parent)  # not the function itself
        relation = FunctionRelation(label, source.columns, self.inputs(source.inputs, before, env))
        return source.alias or "", relation, None

    def table(self, table: TableName, env: Optional[_Env]) -> Relation:
        if table.dbt is None and table.name is not None and "." not in table.name:
            entry = env.find(table.name) if env is not None else None
            if entry is not None:
                return self.cte(entry)
            if table.name in self.temps:
                return self.temps[table.name]
        return self.catalog(table)

    def catalog(self, table: TableName) -> TableRelation:
        """A table the catalog knows, placed where it is read so a failed match is reported there."""
        info = self.lookup(table)
        spec = self.spec(table.start, table.end) if table.end > table.start else (table.name or "{}")
        return TableRelation(info._replace(spec=spec, start=table.start))

    def too_deep(self, spec: str, start: int) -> Relation:
        relation = _TooDeep(spec, spec, start)
        self.record(relation.miss, spec, start)
        return relation

    def cte(self, entry: list) -> Relation:
        cte, relation, env = entry
        if relation is not None:
            if id(entry) in self.reading:
                self.reading[id(entry)] = True
            return relation
        if cte.query is None:
            return self.settle(entry, TableRelation(TableInfo(None, "unresolved", None, None, False, False,
                                                              "dynamic", (), f"cte:{cte.name}", cte.start)))
        if self.depth >= MAX_DEPTH:
            return self.settle(entry, self.too_deep(f"cte:{cte.name}", cte.start))
        label = f"cte:{cte.name}"
        entry[1] = QueryRelation(label, [], [])  # a CTE reading itself sees no columns while it is read
        self.depth += 1
        try:
            if cte.recursive and isinstance(cte.query.body, SetOp):
                body = self.recursive(entry, cte.query, env, label)
            else:
                found = self.query(cte.query, None, env, label)
                body = found.outputs, found.stars, found.cut
        finally:
            self.depth -= 1
        return self.settle(entry, _renamed(label, body, cte.columns))

    def settle(self, entry: list, relation: Relation) -> Relation:
        entry[1] = relation
        self.filled.append(entry)
        return relation

    def recursive(self, entry: list, query: Query, env: Optional[_Env], label: str) -> Body:
        """A recursive CTE's columns come from its first branch. A later branch that reads the CTE itself
        is left out with everything it would record; a later branch that does not is an ordinary branch."""
        if query.ctes:
            env = _Env(env)
            for inner in query.ctes:
                env.entries[inner.name] = [inner, None, env]
        first = self.body(query.body.branches[0], None, env)
        entry[1] = _renamed(label, first, entry[0].columns)
        kept = [first]
        for branch in query.body.branches[1:]:
            usages, missed, filled, reported = len(self.usages), len(self.missed), len(self.filled), set(self.reported)
            self.reading[id(entry)] = False
            try:
                result = self.body(branch, None, env)
            finally:
                reads_itself = self.reading.pop(id(entry))
            if reads_itself:
                del self.usages[usages:], self.missed[missed:]
                self.reported = reported
                for forgotten in self.filled[filled:]:  # a CTE first read here is read again from scratch
                    forgotten[1] = None
                del self.filled[filled:]
                continue
            kept.append(result)
        outputs, stars, cut = self.merge(kept, query.body.start)
        self.uses(query.orders, Scope([], (), None), env, "order_key", outputs, _width(outputs, cut))
        return outputs, stars, cut

    def inputs(self, expr: Expr, scope: Scope, env: Optional[_Env]) -> Callable[[], Result]:
        """The leaves behind a table function's arguments, as one relation column: read the first time
        a column of the function is, and only once."""
        memo: List[Result] = []

        def read() -> Result:
            if not memo:
                leaves, misses = self.value(expr, scope, env, record=False)
                memo.append((leaves, misses[0] if misses and not leaves else None))
            return memo[0]
        return read

    def expand(self, star, scope: Scope, env: Optional[_Env], stars: List[Relation], room: int
               ) -> Tuple[List[Output], Optional[Cut]]:
        """The star's columns, and where among them the first source with columns not all known starts."""
        chosen = scope.sources
        if star.qualifier is not None:
            chosen = named(scope.sources, ".".join(star.qualifier))
            if len(chosen) > 1:  # which source the star reads is unknown: its columns are too
                stars.extend(relation for _, relation, _ in chosen)
                candidates = tuple(sorted(full or relation.label or alias for alias, relation, full in chosen))
                miss = Miss("ambiguous", candidates)
                self.missed.append(Missed(miss, self.spec(star.start, star.end), star.start))
                return [], Cut(0, miss, star.start, star.end)
        outputs: List[Output] = []
        cut: Optional[Cut] = None
        replace = dict(star.replace)
        rename = {old: (new, written) for old, new, written in star.rename}
        for alias, relation, full in chosen:
            names, is_open = _known(relation)
            if is_open:
                stars.append(relation)
                resolved = relation.info.table if isinstance(relation, TableRelation) else None
                miss = Miss("unknown_star", source=resolved or full or relation.label or alias)
                self.missed.append(Missed(miss, self.spec(star.start, star.end), star.start))
                if cut is None:
                    cut = Cut(len(outputs), miss, star.start, star.end)
            for name in names:
                if len(outputs) >= room:
                    self.missed.append(Missed(Miss("too_many_columns"), "*", star.start))
                    return outputs, cut
                if name in star.except_:
                    continue
                if name in replace:
                    leaves, misses = self.value(replace[name], scope, env)
                    outputs.append(Output(name, name, tuple(leaves), tuple(misses), star.start, star.end, False, False))
                    continue
                leaves, miss = relation.column(name, True)
                new, written = rename.get(name, (name, name))
                outputs.append(Output(new, written, tuple(cap(leaf, "inferred", "star") for leaf in dedupe(leaves)),
                                      (miss,) if miss else (), star.start, star.end, False, False))
        return outputs, cut

    # -- expressions -----------------------------------------------------------------
    def value(self, expr: Expr, scope: Scope, env: Optional[_Env], record: bool = True
              ) -> Tuple[List[Leaf], List[Miss]]:
        """Leaves behind an expression's value; window keys inside it become order_key uses."""
        leaves: List[Leaf] = []
        misses: List[Miss] = []
        for ref in expr.refs:
            if ref.context & ORDER:
                if record:
                    self.use_ref(ref, scope, "order_key", [])
                continue
            kind = "pass" if expr.bare else "window" if ref.context & WINDOW else \
                "aggregate" if ref.context & AGG else "transform"
            found, miss = scope.resolve(ref.parts)
            leaves.extend(_combine(leaf, kind, bool(ref.context & CONDITION)) for leaf in found)
            if miss is not None:
                misses.append(miss)
                if record:
                    self.record(miss, ".".join(ref.parts), ref.start)
        for query, context in expr.subqueries:
            relation = self.query(query, scope, env, f"subquery:{self.line(query.start)}")
            kind = "window" if context & WINDOW else "aggregate" if context & AGG else "transform"
            if relation.cut is not None and relation.cut.at == 0:  # its first column is unknown
                misses.append(relation.cut.miss)
                continue
            for output in relation.outputs[:1]:
                leaves.extend(_combine(leaf, kind, bool(context & CONDITION)) for leaf in output.leaves)
        return leaves, misses

    def uses(self, exprs, scope: Scope, env: Optional[_Env], kind: str, outputs: List[Output],
             width: Optional[int] = None) -> None:
        """`width`: how many outputs have a known place, for GROUP BY 1."""
        width = len(outputs) if width is None else width
        for expr in exprs:
            if not expr.refs and not expr.subqueries and self.text[expr.start:expr.end].isdigit():
                index = int(self.text[expr.start:expr.end]) - 1  # GROUP BY 1
                if 0 <= index < width:
                    for leaf in outputs[index].leaves:
                        self.usages.append(Usage(leaf, kind, expr.start))
                continue
            for ref in expr.refs:
                self.use_ref(ref, scope, "order_key" if ref.context & ORDER and kind == "filter" else kind, outputs)
            for query, _ in expr.subqueries:
                relation = self.query(query, scope, env, f"subquery:{self.line(query.start)}")
                for output in relation.outputs:
                    for leaf in output.leaves:
                        self.usages.append(Usage(leaf, kind, query.start))

    def use_ref(self, ref, scope: Scope, kind: str, outputs: List[Output]) -> None:
        found, miss = scope.resolve(ref.parts)
        if miss is not None and miss.reason == "unknown_column" and len(ref.parts) == 1:
            named = [output for output in outputs if output.name == ref.parts[0]]
            if named:  # a SELECT alias used in WHERE, GROUP BY, ORDER BY or QUALIFY
                found = [cap(leaf, "inferred", "select_alias_ref") for output in named for leaf in output.leaves]
                miss = None
        for leaf in found:
            self.usages.append(Usage(leaf, kind, ref.start))
        if miss is not None:
            self.record(miss, ".".join(ref.parts), ref.start)

    def record(self, miss: Miss, spec: str, start: int) -> None:
        """A table that cannot be matched, or the depth limit, is reported once where it is read,
        not for each column."""
        if (miss.reason.startswith("table_") or miss.reason == "too_deep") and miss.start >= 0:
            if (miss.reason, miss.start) in self.reported:
                return
            self.reported.add((miss.reason, miss.start))
            spec, start = miss.source or "", miss.start
        self.missed.append(Missed(miss, spec, start))


def _combine(leaf: Leaf, kind: str, condition: bool) -> Leaf:
    stronger = kind if KIND_RANK[kind] > KIND_RANK[leaf.kind] else leaf.kind
    return leaf._replace(kind=stronger, condition=leaf.condition or condition)


def _width(outputs: List[Output], cut: Optional[Cut]) -> int:
    return len(outputs) if cut is None else cut.at


def _fits(first: List[Output], first_cut: Optional[Cut], other: List[Output], other_cut: Optional[Cut]) -> bool:
    """Whether two set operation branches can have the same width. A branch with an unknown star has
    at least as many columns as it lists."""
    if first_cut is None and other_cut is None:
        return len(first) == len(other)
    if first_cut is None:
        return len(other) <= len(first)
    if other_cut is None:
        return len(first) <= len(other)
    return True


def _renamed(label: str, body: Body, columns: Tuple[str, ...]) -> QueryRelation:
    """A CTE or derived table with a column list names its columns by place. The list is the whole set
    of columns; a name at or after an unknown star's place has no known source."""
    outputs, stars, cut = body
    if not columns:
        return QueryRelation(label, outputs, stars, cut)
    known = outputs if cut is None else outputs[:cut.at]
    named_ = [output._replace(name=name, display=name) for output, name in zip(known, columns)]
    if cut is not None:
        named_ += [Output(name, name, (), (cut.miss,), cut.start, cut.end, False, False)
                   for name in columns[len(named_):]]
    return QueryRelation(label, named_, [] if cut is not None else stars)


def _known(relation: Relation) -> Tuple[Tuple[str, ...], bool]:
    """(the column names a relation is known to have, whether it may have more)."""
    if isinstance(relation, TableRelation):
        return relation.info.names or (), relation.info.names is None or relation.info.open
    if isinstance(relation, QueryRelation):  # each name once: a duplicate name reads both columns anyway
        return tuple(relation.by_name), bool(relation.stars)
    return tuple(relation.names() or ()), relation.names() is None


def derive(text: str, statement: Statement, lookup: Lookup, temps: Dict[str, QueryRelation],
           newlines: Optional[List[int]] = None) -> Derived:
    """Trace one parsed statement. `temps` holds the file's temporary tables made so far;
    `newlines` the places of "\n" in `text`, computed once per file."""
    if newlines is None:
        newlines = [i for i, char in enumerate(text) if char == "\n"]
    deriver = Deriver(text, lookup, temps, newlines)
    outputs: List[Output] = []
    stars: List[Relation] = []
    cut: Optional[Cut] = None
    if statement.query is not None:
        relation = deriver.query(statement.query, None, None, "")
        outputs, stars, cut = relation.outputs, relation.stars, relation.cut
    elif statement.merge is not None:
        outputs = _merge(deriver, statement)
    return Derived(statement, tuple(outputs), tuple(deriver.usages), tuple(deriver.missed), tuple(stars), cut)


def _merge(deriver: Deriver, statement: Statement) -> List[Output]:
    merge = statement.merge
    target = deriver.catalog(statement.target)
    scope = Scope([(merge.target_alias or (statement.target.name or "").rsplit(".", 1)[-1], target,
                    statement.target.name)], (), None)
    scope.sources.append(deriver.source(merge.source, scope, None, None))
    deriver.uses([merge.on], scope, None, "join_key", [])
    deriver.uses(merge.conditions, scope, None, "filter", [])
    outputs = []
    for assignment in merge.updates + merge.inserts:
        leaves, misses = deriver.value(assignment.expr, scope, None)
        expr = assignment.expr
        outputs.append(Output(assignment.column, assignment.display, tuple(leaves), tuple(misses), expr.start,
                              expr.end, not expr.refs and not expr.dynamic, expr.dynamic))
    return outputs
