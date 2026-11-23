from arche_lib import lineage

WRITERS = lineage.index([("w1.sql", "{}.ledger"), ("w2.sql", "acct.ledger"), ("w3.sql", "x.only"),
                         ("w4.sql", "acct.ledger")])


def test_index_lists_sorted_files_per_table_and_tables_per_name():
    assert WRITERS == {
        "files": {"{}.ledger": ["w1.sql"], "acct.ledger": ["w2.sql", "w4.sql"], "x.only": ["w3.sql"]},
        "names": {"ledger": ["acct.ledger", "{}.ledger"], "only": ["x.only"]},
    }


def test_a_dynamic_table_name_is_never_resolved():
    assert lineage.resolve("ledger_{}", True, WRITERS) == [
        {"status": "unresolved", "spec": "ledger_{}", "reason": "dynamic"}]


def test_a_fixed_name_is_verified_against_every_file_that_writes_it():
    assert lineage.resolve("acct.ledger", False, WRITERS) == [
        {"status": "verified", "to": "w2.sql", "rule": "name"}, {"status": "verified", "to": "w4.sql", "rule": "name"}]


def test_a_variable_schema_is_inferred_from_the_table_name():
    assert lineage.resolve("{}.only", False, WRITERS) == [{"status": "inferred", "to": "w3.sql", "rule": "table_name"}]
    assert lineage.resolve("only", False, WRITERS) == [{"status": "inferred", "to": "w3.sql", "rule": "table_name"}]


def test_the_same_variable_schema_on_both_sides_is_not_verified():
    assert lineage.resolve("{}.ledger", False, WRITERS) == [
        {"status": "unresolved", "spec": "{}.ledger", "reason": "ambiguous", "candidates": ["acct.ledger", "{}.ledger"]}]
    alone = lineage.index([("w.sql", "{}.daily")])
    assert lineage.resolve("{}.daily", False, alone) == [{"status": "inferred", "to": "w.sql", "rule": "table_name"}]


def test_a_fixed_schema_matches_a_writer_whose_schema_is_variable():
    assert lineage.resolve("other.ledger", False, WRITERS) == [
        {"status": "inferred", "to": "w1.sql", "rule": "table_name"}]


def test_a_fixed_schema_never_matches_another_fixed_schema():
    assert lineage.resolve("y.only", False, WRITERS) == [{"status": "external", "table": "y.only"}]


def test_a_table_nothing_writes_is_external():
    assert lineage.resolve("none.t", False, WRITERS) == [{"status": "external", "table": "none.t"}]


def test_a_fixed_schema_matches_a_writer_written_without_a_schema():
    writers = lineage.index([("w.sql", "ledger"), ("x.sql", "other.ledger")])

    assert lineage.resolve("acct.ledger", False, writers) == [{"status": "inferred", "to": "w.sql", "rule": "table_name"}]
    assert lineage.resolve("ledger", False, writers) == [{"status": "verified", "to": "w.sql", "rule": "name"}]


def test_a_catalog_prefix_on_one_side_is_inferred():
    writers = lineage.index([("w.sql", "dw.fa"), ("x.sql", "hive.dw.fb")])

    assert lineage.resolve("hive.dw.fa", False, writers) == [{"status": "inferred", "to": "w.sql", "rule": "table_name"}]
    assert lineage.resolve("dw.fb", False, writers) == [{"status": "inferred", "to": "x.sql", "rule": "table_name"}]
    assert lineage.resolve("other.fa", False, writers) == [{"status": "external", "table": "other.fa"}]
    assert lineage.resolve("spark.other.fa", False, writers) == [{"status": "external", "table": "spark.other.fa"}]


def test_catalog_matching_only_compares_writers_with_the_same_table_name(monkeypatch):
    writers = lineage.index([("w.sql", "dw.fa")] + [(f"o{i}.sql", f"s{i}.other{i}") for i in range(50)])
    calls = []
    original = lineage._differs_by_catalog
    monkeypatch.setattr(lineage, "_differs_by_catalog", lambda a, b: calls.append(b) or original(a, b))

    assert lineage.resolve("hive.dw.fa", False, writers) == [{"status": "inferred", "to": "w.sql", "rule": "table_name"}]
    assert calls == ["dw.fa"]
