import pytest

from arche_lib.errors import ParseError
from arche_lib.langs import python_extract


def _imports(source):
    return python_extract.extract("m.py", source)["imports"]


def test_plain_and_aliased_imports_produce_one_record_per_module():
    found = _imports("import os, sys\nimport a.b as c\n")

    assert [(i["kind"], i["module"], i["names"], i["line"]) for i in found] == [
        ("import", "os", [], 1),
        ("import", "sys", [], 1),
        ("import", "a.b", [], 2),
    ]


def test_from_imports_keep_names_and_relative_level():
    found = _imports("from a.b import c, d\nfrom . import x\nfrom ..pkg import y\nfrom m import *\n")

    assert [(i["module"], i["level"], i["names"]) for i in found] == [
        ("a.b", 0, ["c", "d"]),
        ("", 1, ["x"]),
        ("pkg", 2, ["y"]),
        ("m", 0, ["*"]),
    ]


def test_quote_is_the_stripped_first_line_of_the_statement():
    found = _imports("if True:\n    from a import (\n        b,\n    )\n")

    assert found[0]["quote"] == "from a import ("
    assert found[0]["line"] == 2


def test_module_level_imports_are_unconditional():
    found = _imports("import os\n")

    assert (found[0]["conditional"], found[0]["type_only"], found[0]["dynamic"]) == (False, False, False)


@pytest.mark.parametrize(
    "source",
    [
        "try:\n    import a\nexcept ImportError:\n    pass\n",
        "if flag:\n    import a\n",
        "def load():\n    import a\n",
        "class K:\n    import a\n",
        "with ctx:\n    import a\n",
    ],
)
def test_imports_inside_branches_functions_and_classes_are_conditional(source):
    assert _imports(source)[0]["conditional"] is True


def test_type_checking_block_is_type_only_but_its_else_branch_is_not():
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    from a import A\nelse:\n    from b import B\n"
        "if typing.TYPE_CHECKING:\n    import c\n"
    )

    found = {i["module"]: i for i in _imports(source)}

    assert (found["a"]["type_only"], found["a"]["conditional"]) == (True, True)
    assert (found["b"]["type_only"], found["b"]["conditional"]) == (False, True)
    assert found["c"]["type_only"] is True


def test_literal_dynamic_imports_keep_the_module_name():
    found = _imports('import importlib\nm = importlib.import_module("a.b")\nn = __import__("c")\n')

    dynamic = [(i["kind"], i["module"], i["dynamic"], i["line"]) for i in found if i["dynamic"]]
    assert dynamic == [("dynamic", "a.b", True, 2), ("dynamic", "c", True, 3)]


@pytest.mark.parametrize(
    "call",
    ["importlib.import_module(name)", "__import__(name)", 'import_module(".rel", package="p")',
     "importlib.import_module()"],
)
def test_non_literal_or_relative_dynamic_imports_have_no_module(call):
    found = [i for i in _imports("x = %s\n" % call) if i["dynamic"]]

    assert [i["module"] for i in found] == [None]


def test_top_level_symbols_cover_functions_classes_and_assignments_only():
    source = (
        "import os\n"
        "LIMIT = 3\n"
        "count: int = 0\n"
        "_cache = {}\n"
        "def run():\n    def inner():\n        pass\n"
        "async def fetch():\n    pass\n"
        "class Job:\n    def method(self):\n        pass\n"
        "LIMIT = 4\n"
    )

    symbols = python_extract.extract("m.py", source)["symbols"]

    assert [(s["name"], s["kind"], s["line"], s["exported"]) for s in symbols] == [
        ("LIMIT", "variable", 2, True),
        ("count", "variable", 3, True),
        ("_cache", "variable", 4, False),
        ("run", "function", 5, True),
        ("fetch", "function", 8, True),
        ("Job", "class", 10, True),
    ]


def test_syntax_error_raises_parse_error():
    with pytest.raises(ParseError, match="broken.py"):
        python_extract.extract("broken.py", "def (:\n")
