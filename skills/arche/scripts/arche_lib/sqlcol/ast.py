"""Immutable parse results. A query keeps only what column lineage needs: column
references with the context they sit in, sources and their aliases, and places."""
from __future__ import annotations

from typing import NamedTuple, Optional, Tuple, Union

# Context bits of a column reference.
AGG, WINDOW, CONDITION, ORDER = 1, 2, 4, 8


class Ref(NamedTuple):
    parts: Tuple[str, ...]  # lowercase, quotes removed: ("o", "amt")
    display: str  # the last part as written
    start: int
    context: int  # AGG | WINDOW | CONDITION | ORDER


class Expr(NamedTuple):
    start: int
    end: int
    refs: Tuple[Ref, ...]
    subqueries: Tuple[Tuple["Query", int], ...]  # scalar, IN and EXISTS subqueries with their context
    bare: bool  # exactly one column reference and nothing else
    dynamic: bool  # holds a run-time value such as {{ var('x') }}
    calls: bool = False  # holds a function call (COUNT(*) and ROW_NUMBER() are not constants)


class Star(NamedTuple):
    qualifier: Optional[Tuple[str, ...]]  # ("o",) for o.*, None for *
    start: int
    end: int
    except_: Tuple[str, ...] = ()
    replace: Tuple[Tuple[str, Expr], ...] = ()
    rename: Tuple[Tuple[str, str, str], ...] = ()  # (old, new, new as written)


class Item(NamedTuple):
    name: Optional[str]  # the output name, lowercase; None for an expression without a name
    display: Optional[str]
    expr: Optional[Expr]
    star: Optional[Star]
    start: int
    end: int


class TableName(NamedTuple):
    name: Optional[str]  # as sql_tables.table_name gives it; None when it is not a table name
    dynamic: bool
    dbt: Optional[Tuple[str, Optional[Tuple[str, ...]], Optional[str]]]  # ("ref", ("m",), None): {{ ref('m') }}
    start: int
    end: int = 0


class TableSource(NamedTuple):
    table: TableName
    alias: str
    columns: Tuple[str, ...]  # a column list after the alias renames the columns


class QuerySource(NamedTuple):
    query: "Query"
    alias: Optional[str]
    columns: Tuple[str, ...]
    start: int


class FunctionSource(NamedTuple):
    """UNNEST, LATERAL VIEW, FLATTEN and other table functions: every column comes from the inputs."""
    inputs: Expr
    alias: Optional[str]
    columns: Tuple[str, ...]
    start: int


Source = Union[TableSource, QuerySource, FunctionSource]


class Select(NamedTuple):
    items: Tuple[Item, ...]
    sources: Tuple[Source, ...]
    using: Tuple[Tuple[str, ...], ...]
    joins: Tuple[Expr, ...]
    where: Tuple[Expr, ...]  # WHERE, HAVING, QUALIFY
    groups: Tuple[Expr, ...]
    orders: Tuple[Expr, ...]  # ORDER BY, SORT BY, DISTRIBUTE BY, CLUSTER BY, named windows
    start: int


class Values(NamedTuple):
    rows: Tuple[Tuple[Expr, ...], ...]
    start: int


class SetOp(NamedTuple):
    branches: Tuple["Body", ...]
    start: int


class Cte(NamedTuple):
    name: str
    columns: Tuple[str, ...]
    query: Optional["Query"]  # None when a run-time value ({{ macro() }}) is the whole body
    recursive: bool
    start: int


class Query(NamedTuple):
    ctes: Tuple[Cte, ...]
    body: "Body"
    orders: Tuple[Expr, ...]
    start: int


Body = Union[Select, Values, SetOp, Query]


class Assignment(NamedTuple):
    column: str
    display: str
    expr: Expr


class Statement(NamedTuple):
    """One SQL statement. `kind`: query, insert, create, ddl, merge."""
    kind: str
    start: int
    target: Optional[TableName] = None
    columns: Optional[Tuple[Tuple[str, str], ...]] = None  # (name, as written) of a column list
    query: Optional[Query] = None
    temp: bool = False
    constants: Tuple[Assignment, ...] = ()  # static partition values
    tail: bool = False  # tokens were left after the statement
    merge: Optional["Merge"] = None
    partitions: Tuple[Tuple[str, str], ...] = ()  # Hive dynamic partition columns, filled from the end


class Merge(NamedTuple):
    source: Source
    target_alias: Optional[str]
    on: Expr
    conditions: Tuple[Expr, ...]
    updates: Tuple[Assignment, ...]
    inserts: Tuple[Assignment, ...]
