import json

from arche_lib import column_impact


def _value(source, to, kind="direct", status="verified", path="a.sql", line=1, expression="x", role=None):
    edge = {"from": source, "to": to, "kind": kind, "status": status, "rule": "qualified",
            "expression": expression, "via": [], "evidence": {"path": path, "line": line, "quote": ""}}
    if role:
        edge["role"] = role
    return edge


def _usage(source, table, kind="filter", status="verified", path="a.sql"):
    return {"from": source, "to": table, "kind": kind, "status": status, "rule": "listed", "via": [],
            "evidence": {"path": path, "line": 1, "quote": ""}}


def _column(table, name, sql=True, path="a.sql"):
    entry = {"id": f"col:{table}.{name}", "table": table, "column": name, "display": name,
             "known_from": ["sql_output"] if sql else [], "external": not sql}
    if sql:
        entry["defined"] = {"path": path, "line": 1, "quote": ""}
    return entry


def test_walk_tracks_depth_kinds_files_and_stops_at_usages():
    edges = [_value("col:a.x", "col:b.x", path="b.sql"),
             _value("col:b.x", "col:c.y", kind="rename", path="c.sql"),
             _value("col:a.x", "col:c.y", kind="aggregate", path="c2.sql"),
             _usage("col:c.y", "table:d", kind="group_key", path="d.sql"),
             _usage("col:c.y", "table:d", kind="join_key", path="d.sql"),
             _value("col:zz.q", "col:d.q")]

    result = column_impact.downstream(edges, ["col:a.x"])

    assert result["affected"] == [
        {"column": "col:b.x", "depth": 1, "kinds": ["direct"], "status": "verified", "files": ["b.sql"]},
        {"column": "col:c.y", "depth": 1, "kinds": ["aggregate", "rename"], "status": "verified",
         "files": ["c.sql", "c2.sql"]}]
    assert result["used_by"] == [{"table": "table:d", "kinds": ["group_key", "join_key"], "status": "verified",
                                  "files": ["d.sql"]}]
    assert result["truncated"] is False


def test_path_status_is_the_lowest_edge_and_the_better_path_wins():
    edges = [_value("col:a.x", "col:b.x", status="inferred"),
             _value("col:b.x", "col:c.x"),
             _value("col:c.x", "col:d.x"),
             _value("col:a.x", "col:e.x"),
             _value("col:e.x", "col:f.x", status="inferred"),
             _value("col:a.x", "col:f.x")]

    result = {item["column"]: item for item in column_impact.downstream(edges, ["col:a.x"])["affected"]}

    assert result["col:b.x"]["status"] == "inferred"
    assert result["col:c.x"]["status"] == "inferred"
    assert result["col:d.x"]["status"] == "inferred"
    assert result["col:f.x"]["status"] == "verified"
    assert result["col:d.x"]["depth"] == 3


def test_cycles_terminate_and_seeds_are_not_affected():
    edges = [_value("col:a.x", "col:b.x"), _value("col:b.x", "col:c.x"), _value("col:c.x", "col:a.x"),
             _value("col:c.x", "col:b.x")]

    result = column_impact.downstream(edges, ["col:a.x"])

    assert [item["column"] for item in result["affected"]] == ["col:b.x", "col:c.x"]


def test_limit_sets_truncated():
    edges = [_value("col:s.x", f"col:t.c{i:03}") for i in range(20)] + [_usage("col:s.x", "table:u")]

    result = column_impact.downstream(edges, ["col:s.x"], limit=5)

    assert len(result["affected"]) + len(result["used_by"]) == 5
    assert result["truncated"] is True
    assert column_impact.downstream(edges, ["col:s.x"], limit=21)["truncated"] is False


def test_long_chain_is_linear_enough():
    edges = [_value(f"col:t.c{i}", f"col:t.c{i + 1}") for i in range(20000)]

    result = column_impact.downstream(edges, ["col:t.c0"], limit=50000)

    assert len(result["affected"]) == 20000
    assert max(item["depth"] for item in result["affected"]) == 20000


def test_diff_finds_removed_added_and_redefined_columns():
    base = {"columns": [_column("raw", "a", sql=False), _column("s", "gone", path="s.sql"),
                        _column("s", "same", path="s.sql"), _column("s", "changed", path="s.sql")],
            "edges": [_value("col:raw.a", "col:s.gone", path="s.sql"),
                      _value("col:raw.a", "col:s.same", path="s.sql"),
                      _value("col:raw.a", "col:s.changed", path="s.sql", expression="a")]}
    head = {"columns": [_column("raw", "a", sql=False), _column("s", "same", path="s.sql"),
                        _column("s", "changed", path="s.sql"), _column("s", "new", path="s2.sql")],
            "edges": [_value("col:raw.a", "col:s.same", path="s.sql", line=9),
                      _value("col:raw.a", "col:s.changed", path="s.sql", expression="a + 1"),
                      _value("col:raw.a", "col:s.new", path="s2.sql")]}

    seeds = column_impact.seeds_from_diff(base, head)

    assert seeds == [
        {"column": "col:s.changed", "change": "redefined", "seed": "column_diff", "path": "s.sql"},
        {"column": "col:s.gone", "change": "removed", "seed": "column_diff", "path": "s.sql"},
        {"column": "col:s.new", "change": "added", "seed": "column_diff", "path": "s2.sql"}]


def test_role_change_redefines_and_edge_path_stands_in_for_defined():
    base = {"columns": [{"id": "col:s.x", "table": "s", "column": "x", "known_from": []}],
            "edges": [_value("col:r.a", "col:s.x", path="s.sql")]}
    head = {"columns": [{"id": "col:s.x", "table": "s", "column": "x", "known_from": []}],
            "edges": [_value("col:r.a", "col:s.x", path="s.sql", role="condition")]}

    assert column_impact.seeds_from_diff(base, head) == [
        {"column": "col:s.x", "change": "redefined", "seed": "column_diff", "path": "s.sql"}]


def test_file_seeds_are_columns_written_by_changed_files():
    doc = {"columns": [], "edges": [_value("col:r.a", "col:s.x", path="s.sql"),
                                    _value("col:r.b", "col:s.x", path="other.sql"),
                                    _value(None, "col:s.dt", kind="constant", path="s.sql"),
                                    _usage("col:r.a", "table:s", path="s.sql"),
                                    _value("col:r.a", "col:t.y", path="t.sql")]}

    assert column_impact.seeds_from_files(doc, ["s.sql", "other.sql"]) == [
        {"column": "col:s.dt", "change": "file", "seed": "file", "path": "s.sql"},
        {"column": "col:s.x", "change": "file", "seed": "file", "path": "other.sql"}]


def _pipeline():
    base = {"columns": [_column("raw", "a", sql=False), _column("s", "old", path="s.sql"),
                        _column("m", "v", path="m.sql")],
            "edges": [_value("col:raw.a", "col:s.old", path="s.sql"),
                      _value("col:s.old", "col:m.v", kind="rename", path="m.sql"),
                      _usage("col:s.old", "table:query:report.sql#1", path="report.sql")]}
    head = {"columns": [_column("raw", "a", sql=False), _column("s", "new", path="s.sql"),
                        _column("m", "w", path="m.sql")],
            "edges": [_value("col:raw.a", "col:s.new", path="s.sql"),
                      _value("col:s.new", "col:m.w", kind="aggregate", status="inferred", path="m.sql")]}
    return base, head


def test_build_walks_removed_seeds_on_base_and_the_rest_on_head():
    base, head = _pipeline()

    result = column_impact.build(head, ["s.sql"], base)

    assert [(s["column"], s["change"]) for s in result["seeds"]] == [
        ("col:m.v", "removed"), ("col:m.w", "added"), ("col:s.new", "added"), ("col:s.old", "removed")]
    assert result["affected"] == []
    assert result["used_by"] == [{"table": "table:query:report.sql#1", "kinds": ["filter"], "status": "verified",
                                  "files": ["report.sql"]}]
    assert result["affected_files"] == ["report.sql"]
    assert result["affected_tables"] == ["query:report.sql#1"]
    assert result["available"] is True and result["truncated"] is False


def test_build_with_one_snapshot_uses_file_seeds():
    _, head = _pipeline()

    result = column_impact.build(head, ["s.sql"])

    assert result["seeds"] == [{"column": "col:s.new", "change": "file", "seed": "file", "path": "s.sql"}]
    assert result["affected"] == [{"column": "col:m.w", "depth": 1, "kinds": ["aggregate"], "status": "inferred",
                                   "files": ["m.sql"]}]
    assert result["affected_tables"] == ["m"]
    assert result["affected_files"] == ["m.sql"]
    assert list(result) == ["available", "seeds", "affected", "used_by", "affected_files", "affected_tables",
                            "truncated"]


def test_build_is_deterministic_regardless_of_input_order():
    base, head = _pipeline()
    shuffled = {**head, "edges": list(reversed(head["edges"])), "columns": list(reversed(head["columns"]))}

    first = json.dumps(column_impact.build(head, ["s.sql", "m.sql"], base), sort_keys=False)
    second = json.dumps(column_impact.build(shuffled, ["m.sql", "s.sql"], base), sort_keys=False)

    assert first == second


def test_build_tolerates_malformed_entries_and_empty_shape():
    doc = {"columns": "nope", "edges": [None, {"from": 1, "to": "col:x.y"}, {"to": None}]}

    result = column_impact.build(doc, ["a.sql"], {"columns": [], "edges": []})

    assert result == {**column_impact.empty(True)}
    assert column_impact.empty() == {"available": False, "seeds": [], "affected": [], "used_by": [],
                                     "affected_files": [], "affected_tables": [], "truncated": False}


_HOSTILE = [None, True, 0, 1.5, "", "col:x", [], [1, [2]], {}, {"a": []}]


def _hostile_documents():
    good_edge = _value("col:s.a", "col:m.b", path="a.sql")
    good_column = _column("s", "a")
    yield from _HOSTILE
    for value in _HOSTILE:
        yield {"columns": value, "edges": value}
        yield {"columns": [value], "edges": [value, good_edge]}
        for field in ("from", "to", "kind", "status", "role", "expression", "evidence"):
            yield {"columns": [good_column], "edges": [{**good_edge, field: value},
                                                       _usage("col:s.a", "table:u", path="a.sql") | {field: value}]}
        yield {"columns": [good_column], "edges": [{**good_edge, "evidence": {"path": value}}]}
        for field in ("id", "table", "known_from", "defined"):
            yield {"columns": [{**good_column, field: value}], "edges": [good_edge]}
        yield {"columns": [{**good_column, "defined": {"path": value}}], "edges": [good_edge]}


def test_build_never_raises_on_hostile_documents():
    documents = list(_hostile_documents())
    good = {"columns": [_column("s", "a")], "edges": [_value("col:s.a", "col:m.b", path="a.sql")]}

    for doc in documents:
        column_impact.build(doc, ["a.sql"])
        column_impact.build(doc, ["a.sql"], good)
        column_impact.build(good, ["a.sql"], doc)
        column_impact.downstream(doc.get("edges") if isinstance(doc, dict) and isinstance(doc.get("edges"), list)
                                 else [], ["col:s.a"])


def test_unhashable_status_or_kind_skips_the_edge():
    doc = {"columns": [], "edges": [_value("col:r.a", "col:a.x"),
                                    {**_value("col:a.x", "col:b.x", path="b.sql"), "status": []},
                                    {**_usage("col:a.x", "table:z", path="b.sql"), "kind": []},
                                    _value("col:a.x", "col:c.x", path="b.sql")]}

    result = column_impact.build(doc, ["a.sql"])

    assert [a["column"] for a in result["affected"]] == ["col:c.x"]
    assert result["used_by"] == []


def test_a_better_status_found_later_reaches_downstream_columns():
    edges = [_value("col:s.x", "col:b.x", status="inferred"), _value("col:s.x", "col:e.x"),
             _value("col:e.x", "col:f.x"), _value("col:f.x", "col:b.x"), _value("col:b.x", "col:c.x")]

    result = {a["column"]: (a["depth"], a["status"]) for a in column_impact.downstream(edges, ["col:s.x"])["affected"]}

    assert result == {"col:b.x": (1, "verified"), "col:c.x": (2, "verified"), "col:e.x": (1, "verified"),
                      "col:f.x": (2, "verified")}


def test_cap_keeps_the_shallowest_entries_across_affected_and_used_by():
    chain = [_value("col:s.keep" if i == 0 else f"col:t.c{i - 1}", f"col:t.c{i}", path="b.sql") for i in range(3)]
    columns = [_column("t", f"c{i}") for i in range(3)] + [_column("s", "keep")]
    base = {"columns": columns + [_column("s", "gone")],
            "edges": [_value("col:r.a", "col:s.gone", expression="x"), _value("col:r.a", "col:s.keep", expression="x"),
                      _usage("col:s.gone", "table:u", path="u.sql")] + chain}
    head = {"columns": columns, "edges": [_value("col:r.a", "col:s.keep", expression="y")] + chain}

    result = column_impact.build(head, [], base, limit=3)

    assert [(s["column"], s["change"]) for s in result["seeds"]] == [
        ("col:s.gone", "removed"), ("col:s.keep", "redefined")]
    assert [(a["column"], a["depth"]) for a in result["affected"]] == [("col:t.c0", 1), ("col:t.c1", 2)]
    assert [u["table"] for u in result["used_by"]] == ["table:u"]
    assert set(result["used_by"][0]) == {"table", "kinds", "status", "files"}
    assert result["truncated"] is True
