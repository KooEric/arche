import pytest

from arche_lib import fileref


@pytest.mark.parametrize("text, expected", [
    ("sql/load.sql", True), ("settle.HQL", True), ("{{ params.dir }}/load.sql", True),
    (".sql", False), ("select 1 from a.sql", False), ("load.sql ", False), ("notes.txt", False),
    ("a b.sql", False), ("s3://bucket/sql/x.sql", False), ("https://example.com/a.sql", False),
    ("a" * 197 + ".sql", False),
])
def test_only_text_that_can_name_one_sql_file_is_a_path(text, expected):
    assert fileref.is_path(text) is expected


@pytest.mark.parametrize("text, expected", [
    ("sql/load.sql", False), ("sql/{}.sql", True), ("%s.sql", True), ("sql/*.sql", True),
    ("load_?.sql", True), ("{{ params.dir }}/load.sql", True),
])
def test_placeholders_and_globs_make_a_template(text, expected):
    assert fileref.is_template(text) is expected


def test_index_groups_sorted_paths_by_file_name():
    assert fileref.index(["b/x.sql", "a/x.sql", "a/y.hql"]) == {"x.sql": ["a/x.sql", "b/x.sql"], "y.hql": ["a/y.hql"]}


def _resolve(spec, importer, paths, dynamic=False):
    return fileref.resolve(spec, importer, fileref.index(paths), dynamic)


def test_a_dynamic_reference_is_never_resolved():
    assert _resolve("sql/{}.sql", "dags/a.py", ["dags/sql/x.sql"], True) == {
        "status": "unresolved", "spec": "sql/{}.sql", "reason": "dynamic"}


def test_a_path_beside_the_importer_is_verified_first():
    assert _resolve("sql/x.sql", "dags/a/dag.py", ["dags/a/sql/x.sql", "sql/x.sql"]) == {
        "status": "verified", "to": "dags/a/sql/x.sql", "rule": "relative"}


def test_parent_segments_are_followed_only_from_the_importer():
    assert _resolve("../x.sql", "a/b/m.py", ["a/x.sql"])["to"] == "a/x.sql"
    assert _resolve("../../x.sql", "a/m.py", ["a/x.sql"]) == {
        "status": "unresolved", "spec": "../../x.sql", "reason": "not_found"}


def test_a_path_from_the_scan_root_is_verified():
    assert _resolve("dags/b/sql/x.sql", "dags/a/dag.py", ["dags/b/sql/x.sql"]) == {
        "status": "verified", "to": "dags/b/sql/x.sql", "rule": "root"}


def test_backslashes_and_leading_dot_slash_are_normalized():
    assert _resolve(".\\q\\x.sql", "m.py", ["q/x.sql"])["rule"] == "relative"


def test_one_file_ending_with_the_path_is_inferred():
    assert _resolve("x.sql", "dags/a/dag.py", ["dags/a/sql/x.sql"]) == {
        "status": "inferred", "to": "dags/a/sql/x.sql", "rule": "suffix"}


def test_a_suffix_matches_whole_path_segments_only():
    assert _resolve("x.sql", "m.py", ["q/ax.sql"])["reason"] == "not_found"
    assert _resolve("x.sql", "m.py", ["q/x.sql", "q/ax.sql"])["to"] == "q/x.sql"


def test_an_absolute_path_is_matched_by_its_longest_tail():
    assert _resolve("/opt/airflow/q/x.sql", "m.py", ["q/x.sql", "r/x.sql"]) == {
        "status": "inferred", "to": "q/x.sql", "rule": "suffix"}


def test_a_written_directory_is_never_dropped_down_to_the_file_name():
    assert _resolve("sql/x.sql", "m.py", ["other/x.sql"]) == {
        "status": "unresolved", "spec": "sql/x.sql", "reason": "not_found"}
    assert _resolve("/opt/airflow/sql/x.sql", "m.py", ["other/x.sql", "q/sql/x.sql"])["to"] == "q/sql/x.sql"


def test_several_files_ending_with_the_path_are_listed_as_candidates():
    assert _resolve("x.sql", "m.py", ["r/x.sql", "q/x.sql"]) == {
        "status": "unresolved", "spec": "x.sql", "reason": "ambiguous", "candidates": ["q/x.sql", "r/x.sql"]}


def test_a_path_matching_nothing_is_not_found():
    assert _resolve("sql/gone.sql", "m.py", ["sql/x.sql"]) == {
        "status": "unresolved", "spec": "sql/gone.sql", "reason": "not_found"}
