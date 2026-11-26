"""Output columns of each statement in one file, traced to the table columns behind them,
and the columns the statement uses without passing them on (filters, join and group keys)."""
from __future__ import annotations

from bisect import bisect_left
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from . import MAX_DEPTH, MAX_SELECT_ITEMS
from .ast import AGG, CONDITION, ORDER, WINDOW, Expr, Query, QuerySource, Select, SetOp, Statement, \
    TableName, TableSource, Values
from .scope import KIND_RANK, FunctionRelation, Leaf, Miss, Output, QueryRelation, Relation, Result, Scope, \
    TableInfo, TableRelation, cap, dedupe

Lookup = Callable[[TableName], TableInfo]


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


class Deriver:
    def __init__(self, text: str, lookup: Lookup, temps: Dict[str, QueryRelation], newlines: List[int]) -> None:
        self.text, self.lookup, self.temps, self.newlines = text, lookup, temps, newlines
        self.usages: List[Usage] = []
        self.missed: List[Missed] = []
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
            outputs, stars = self.select(query.body, scope, env, query.orders)
        else:  # after a set operation ORDER BY names output columns
            outputs, stars = self.body(query.body, scope, env)
            self.uses(query.orders, Scope([], (), scope), env, "order_key", outputs)
        return QueryRelation(label, outputs, stars)

    def body(self, body, scope: Optional[Scope], env: Optional[_Env]) -> Tuple[List[Output], List[Relation]]:
        if isinstance(body, Query):
            relation = self.query(body, scope, env, "")
            return relation.outputs, relation.stars
        if isinstance(body, Select):
            return self.select(body, scope, env)
        if isinstance(body, Values):
            return self.values(body, scope, env), []
        return self.set_op(body, scope, env)

    def set_op(self, body: SetOp, scope: Optional[Scope], env: Optional[_Env]) -> Tuple[List[Output], List[Relation]]:
        branches = [self.body(branch, scope, env) for branch in body.branches]
        first, stars = branches[0][0], [star for _, branch_stars in branches for star in branch_stars]
        merged = []
        for index, output in enumerate(first):
            leaves, misses, constant, dynamic = list(output.leaves), list(output.misses), output.constant, output.dynamic
            for outputs, branch_stars in branches[1:]:
                if branch_stars:
                    continue
                if len(outputs) != len(first):
                    misses.append(Miss("arity"))
                    continue
                other = outputs[index]
                leaves.extend(other.leaves)
                misses.extend(other.misses)
                constant, dynamic = constant or other.constant, dynamic or other.dynamic
            merged.append(output._replace(leaves=tuple(dedupe(leaves)), misses=tuple(misses), constant=constant,
                                          dynamic=dynamic))
        return merged, stars

    def values(self, body: Values, scope: Optional[Scope], env: Optional[_Env]) -> List[Output]:
        width = max(len(row) for row in body.rows)
        outputs = []
        for index in range(width):
            leaves: List[Leaf] = []
            misses: List[Miss] = []
            dynamic = False
            for row in body.rows:
                if index < len(row):
                    found, missed = self.value(row[index], self.scope_of([], (), scope))
                    leaves.extend(found)
                    misses.extend(missed)
                    dynamic = dynamic or row[index].dynamic
            outputs.append(Output(None, None, tuple(leaves), tuple(misses), body.start, body.start, not leaves,
                                  dynamic))
        return outputs

    def scope_of(self, sources, using, parent) -> Scope:
        return Scope(sources, using, parent)

    def select(self, select: Select, parent: Optional[Scope], env: Optional[_Env], orders: Tuple[Expr, ...] = ()
               ) -> Tuple[List[Output], List[Relation]]:
        scope = Scope([], tuple(name for names in select.using for name in names), parent)
        for source in select.sources:
            scope.sources.append(self.source(source, scope, parent, env))
        outputs: List[Output] = []
        stars: List[Relation] = []
        for item in select.items:
            if len(outputs) > MAX_SELECT_ITEMS:
                self.missed.append(Missed(Miss("too_many_columns"), "*", item.start))
                break
            if item.star is not None:
                outputs.extend(self.expand(item.star, scope, stars, MAX_SELECT_ITEMS - len(outputs)))
                continue
            leaves, misses = self.value(item.expr, scope)
            leaves = dedupe(leaves)
            expr = item.expr
            constant = not expr.refs and not expr.subqueries and not expr.dynamic and not expr.calls
            if expr.dynamic and not leaves:
                misses = [Miss("dynamic")] + misses  # a macro or variable made it; passed on through SELECT *
            outputs.append(Output(item.name, item.display, tuple(leaves), tuple(misses), item.start, item.end,
                                  constant, item.expr.dynamic))
        self.uses(select.joins, scope, env, "join_key", outputs)
        for names in select.using:
            for name in names:
                for _, relation, _ in scope.sources:
                    if relation.has(name) is not False:
                        for leaf in relation.column(name, True)[0]:
                            self.usages.append(Usage(leaf, "join_key", select.start))
        self.uses(select.where, scope, env, "filter", outputs)
        self.uses(select.groups, scope, env, "group_key", outputs)
        self.uses(select.orders + orders, scope, env, "order_key", outputs)
        return outputs, stars

    def source(self, source, scope: Scope, parent: Optional[Scope], env: Optional[_Env]
               ) -> Tuple[str, Relation, Optional[str]]:
        if self.depth >= MAX_DEPTH:
            self.missed.append(Missed(Miss("too_deep"), "", getattr(source, "start", 0)))
            return "", QueryRelation("", [], []), None
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
                relation = QueryRelation(label, [output._replace(name=name, display=name) for output, name
                                                 in zip(relation.outputs, source.columns)], relation.stars)
            return source.alias or "", relation, None
        inputs = source.inputs
        label = f"function:{self.line(source.start)}"
        relation = FunctionRelation(label, source.columns, lambda: self.inputs(inputs, scope))
        return source.alias or "", relation, None

    def table(self, table: TableName, env: Optional[_Env]) -> Relation:
        if table.dbt is None and table.name is not None and "." not in table.name:
            entry = env.find(table.name) if env is not None else None
            if entry is not None:
                return self.cte(entry)
            if table.name in self.temps:
                return self.temps[table.name]
        info = self.lookup(table)
        spec = self.spec(table.start, table.end) if table.end > table.start else (table.name or "{}")
        return TableRelation(info._replace(spec=spec, start=table.start))

    def cte(self, entry: list) -> Relation:
        cte, relation, env = entry
        if relation is None and self.depth >= MAX_DEPTH:
            self.missed.append(Missed(Miss("too_deep"), cte.name, cte.start))
            return QueryRelation(f"cte:{cte.name}", [], [])
        if relation is None and cte.query is None:
            entry[1] = TableRelation(TableInfo(None, "unresolved", None, None, False, False, "dynamic", (),
                                               f"cte:{cte.name}", cte.start))
            return entry[1]
        if relation is None:
            self.depth += 1
            label = f"cte:{cte.name}"
            entry[1] = QueryRelation(label, [], [])  # a CTE reading itself sees no columns while it is read
            body = cte.query.body
            if cte.recursive and isinstance(body, SetOp):
                first, stars = self.body(body.branches[0], None, env)
                entry[1] = QueryRelation(label, self.rename(first, cte.columns), stars)
            relation = self.query(cte.query, None, env, label)
            relation = QueryRelation(label, self.rename(relation.outputs, cte.columns), relation.stars)
            entry[1] = relation
            self.depth -= 1
        return relation

    def inputs(self, expr: Expr, scope: Scope) -> Result:
        """The leaves behind a table function's arguments, as one relation column."""
        leaves, misses = self.value(expr, scope, record=False)
        return leaves, misses[0] if misses and not leaves else None

    @staticmethod
    def rename(outputs: List[Output], columns: Tuple[str, ...]) -> List[Output]:
        if not columns:
            return outputs
        return [output._replace(name=name, display=name) for output, name in zip(outputs, columns)]

    def expand(self, star, scope: Scope, stars: List[Relation], room: int) -> List[Output]:
        chosen = scope.sources
        if star.qualifier is not None:
            qualifier = ".".join(star.qualifier)
            chosen = [s for s in scope.sources if s[0] == qualifier or (s[2] or "").endswith(qualifier)]
        outputs: List[Output] = []
        replace = dict(star.replace)
        rename = {old: (new, written) for old, new, written in star.rename}
        for alias, relation, full in chosen:
            names, is_open = _known(relation)
            if is_open:
                stars.append(relation)
                resolved = relation.info.table if isinstance(relation, TableRelation) else None
                self.missed.append(Missed(Miss("unknown_star", source=resolved or full or relation.label or alias),
                                          self.spec(star.start, star.end), star.start))
            for name in names:
                if len(outputs) >= room:
                    self.missed.append(Missed(Miss("too_many_columns"), "*", star.start))
                    return outputs
                if name in star.except_:
                    continue
                if name in replace:
                    leaves, misses = self.value(replace[name], scope)
                    outputs.append(Output(name, name, tuple(leaves), tuple(misses), star.start, star.end, False, False))
                    continue
                leaves, miss = relation.column(name, True)
                new, written = rename.get(name, (name, name))
                outputs.append(Output(new, written, tuple(cap(leaf, "inferred", "star") for leaf in dedupe(leaves)),
                                      (miss,) if miss else (), star.start, star.end, False, False))
        return outputs

    # -- expressions -----------------------------------------------------------------
    def value(self, expr: Expr, scope: Scope, record: bool = True) -> Tuple[List[Leaf], List[Miss]]:
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
            relation = self.query(query, scope, None, f"subquery:{self.line(query.start)}")
            kind = "window" if context & WINDOW else "aggregate" if context & AGG else "transform"
            for output in relation.outputs[:1]:
                leaves.extend(_combine(leaf, kind, bool(context & CONDITION)) for leaf in output.leaves)
        return leaves, misses

    def uses(self, exprs, scope: Optional[Scope], env: Optional[_Env], kind: str, outputs: List[Output]) -> None:
        for expr in exprs:
            if scope is None:
                continue
            if not expr.refs and not expr.subqueries and self.text[expr.start:expr.end].isdigit():
                index = int(self.text[expr.start:expr.end]) - 1  # GROUP BY 1
                if 0 <= index < len(outputs):
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
        """A table that cannot be matched is reported once, where it is read, not for each column."""
        if miss.reason.startswith("table_") and miss.start >= 0:
            spec, start = miss.source or "", miss.start
        self.missed.append(Missed(miss, spec, start))


def _combine(leaf: Leaf, kind: str, condition: bool) -> Leaf:
    stronger = kind if KIND_RANK[kind] > KIND_RANK[leaf.kind] else leaf.kind
    return leaf._replace(kind=stronger, condition=leaf.condition or condition)


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
    if statement.query is not None:
        relation = deriver.query(statement.query, None, None, "")
        outputs, stars = relation.outputs, relation.stars
    elif statement.merge is not None:
        outputs = _merge(deriver, statement)
    return Derived(statement, tuple(outputs), tuple(deriver.usages), tuple(deriver.missed), tuple(stars))


def _merge(deriver: Deriver, statement: Statement) -> List[Output]:
    merge = statement.merge
    target = TableRelation(deriver.lookup(statement.target))
    scope = Scope([(merge.target_alias or (statement.target.name or "").rsplit(".", 1)[-1], target,
                    statement.target.name)], (), None)
    scope.sources.append(deriver.source(merge.source, scope, None, None))
    deriver.uses([merge.on], scope, None, "join_key", [])
    deriver.uses(merge.conditions, scope, None, "filter", [])
    outputs = []
    for assignment in merge.updates + merge.inserts:
        leaves, misses = deriver.value(assignment.expr, scope)
        expr = assignment.expr
        outputs.append(Output(assignment.column, assignment.display, tuple(leaves), tuple(misses), expr.start,
                              expr.end, not expr.refs and not expr.dynamic, expr.dynamic))
    return outputs
