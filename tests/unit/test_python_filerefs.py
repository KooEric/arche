from arche_lib.langs import python
from arche_lib.langs.python_filerefs import file_refs


def _specs(source):
    return [(ref["line"], ref["spec"], ref["dynamic"]) for ref in file_refs("m.py", source)]


def test_a_literal_path_is_collected_with_its_line_and_quote():
    refs = file_refs("m.py", 'x = 1\nload = Op(task_id="load", sql="sql/load.sql")\n')

    assert refs == [{"spec": "sql/load.sql", "line": 2, "quote": 'load = Op(task_id="load", sql="sql/load.sql")',
                     "dynamic": False}]


def test_the_call_a_path_is_passed_to_does_not_matter():
    source = 'a = read_sql("a.sql")\nb = open("b.hql").read()\nC = "c.SQL"\n'

    assert _specs(source) == [(1, "a.sql", False), (2, "b.hql", False), (3, "c.SQL", False)]


def test_docstrings_and_bare_strings_are_left_out():
    source = '"""x.sql"""\ndef f():\n    "doc.sql"\n    return 1\n'

    assert _specs(source) == []


def test_text_that_is_not_one_path_is_left_out():
    assert _specs('A = "select 1 from a.sql"\nB = ".sql"\nC = "notes.txt"\nD = 4 / 2\n') == []


def test_path_join_keeps_the_trailing_literal_pieces():
    source = (
        'import os\nfrom os.path import join\n'
        'a = os.path.join(BASE, "sql", "a.sql")\n'
        'b = join("q", "b.sql")\n'
        'c = P.joinpath("s", "c.hql")\n'
        'd = os.path.join("s", name, "d.sql")\n'
    )

    assert _specs(source) == [(3, "sql/a.sql", False), (4, "q/b.sql", False), (5, "s/c.hql", False), (6, "d.sql", False)]


def test_pieces_used_in_a_join_are_not_collected_again():
    assert _specs('a = os.path.join("x.sql", "y.sql")\n') == [(1, "x.sql/y.sql", False)]


def test_a_string_join_is_not_a_path_join():
    assert _specs('a = ", ".join(["a.sql"])\n') == [(1, "a.sql", False)]


def test_path_division_keeps_the_trailing_literal_pieces():
    assert _specs('a = (Path(BASE) / "sql" / "a.sql").read_text()\n') == [(1, "sql/a.sql", False)]


def test_formatted_and_templated_paths_are_dynamic():
    source = (
        'a = f"sql/stage_{table}.sql"\n'
        'b = "%s.sql" % table\n'
        'c = "{}.sql".format(table)\n'
        'd = "{{ params.dir }}/d.sql"\n'
        'e = "sql/*.sql"\n'
    )

    assert _specs(source) == [(1, "sql/stage_{}.sql", True), (2, "%s.sql", True), (3, "{}.sql", True),
                              (4, "{{ params.dir }}/d.sql", True), (5, "sql/*.sql", True)]


def test_an_f_string_without_values_is_a_plain_path():
    assert _specs('a = f"plain.sql"\n') == [(1, "plain.sql", False)]


def test_a_file_that_does_not_parse_has_no_references():
    assert file_refs("broken.py", "def (:\n") == []


def test_the_python_adapter_reports_file_references():
    assert [ref["spec"] for ref in python.extract("m.py", 'Q = "q/x.sql"\n')["file_refs"]] == ["q/x.sql"]


def test_concatenated_paths_are_recorded():
    source = (
        'a = "sql/" + name + ".sql"\n'
        'b = "sql/" + "b.sql"\n'
        'c = BASE + "/sql/c.sql"\n'
        'd = os.path.join(BASE, "sql/" + name + ".sql")\n'
        'e = Path(d) / (name + ".hql")\n'
        'f = "total: " + str(n)\n'
    )

    assert _specs(source) == [(1, "sql/{}.sql", True), (2, "sql/b.sql", False), (3, "/sql/c.sql", False),
                              (4, "sql/{}.sql", True), (5, "{}.hql", True)]


def test_a_chained_division_is_collected_once():
    assert _specs('a = Path(B) / "a.sql" / "b.sql"\n') == [(1, "a.sql/b.sql", False)]


def test_a_concatenation_inside_a_join_keeps_the_folder():
    source = (
        'a = os.path.join(BASE, "sql", "x" + ".sql")\n'
        'b = Path(B) / "sql" / ("y" + ".sql")\n'
        'c = os.path.join(BASE, "sql", name + ".sql")\n'
        'd = Path(B) / (name + ".hql")\n'
    )

    assert _specs(source) == [(1, "sql/x.sql", False), (2, "sql/y.sql", False), (3, "sql/{}.sql", True),
                              (4, "{}.hql", True)]


def test_two_paths_added_together_stay_two_paths():
    assert _specs('a = "a.sql" + "b.sql"\n') == [(1, "a.sql", False), (1, "b.sql", False)]
