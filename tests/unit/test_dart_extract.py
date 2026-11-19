import pytest

from arche_lib.langs import dart_extract


def _imports(source):
    return dart_extract.extract("lib/m.dart", source)["imports"]


def _one(source):
    found = _imports(source)
    assert len(found) == 1, found
    return found[0]


def _symbols(source):
    return [(s["name"], s["kind"], s["line"], s["exported"])
            for s in dart_extract.extract("lib/m.dart", source)["symbols"]]


def test_plain_import():
    imp = _one("import 'package:flutter/material.dart';\n")

    assert (imp["kind"], imp["uri"], imp["names"], imp["attrs"]) == (
        "import", "package:flutter/material.dart", [], {})
    assert (imp["line"], imp["quote"]) == (1, "import 'package:flutter/material.dart';")
    assert (imp["reexport"], imp["part"], imp["conditional"]) == (False, False, False)


def test_import_with_prefix_show_and_hide():
    shown = _one('import "models.dart" as models show Order, User;\n')
    hidden = _one("import 'service.dart' hide internalHelper, other;\n")

    assert (shown["names"], shown["attrs"]) == (["Order", "User"], {"prefix": "models"})
    assert (hidden["names"], hidden["attrs"]) == ([], {"hide": ["internalHelper", "other"]})


def test_deferred_import_records_prefix_and_flag():
    imp = _one("import 'heavy.dart' deferred as heavy;\n")

    assert imp["attrs"] == {"prefix": "heavy", "deferred": True}


def test_conditional_import_yields_the_default_and_each_alternative():
    source = (
        "import 'storage_stub.dart'\n"
        "    if (dart.library.io) 'storage_io.dart'\n"
        "    if (dart.library.html) 'storage_web.dart';\n"
    )

    found = _imports(source)

    assert [(i["uri"], i["conditional"], i["line"]) for i in found] == [
        ("storage_stub.dart", False, 1),
        ("storage_io.dart", True, 1),
        ("storage_web.dart", True, 1),
    ]


def test_export_is_a_reexport_and_show_limits_names():
    imp = _one("export 'user.dart' show User;\n")

    assert (imp["kind"], imp["reexport"], imp["names"]) == ("export", True, ["User"])


def test_part_and_part_of_with_a_uri():
    part = _one("part 'order.g.dart';\n")
    part_of = _one("part of 'order.dart';\n")

    assert (part["kind"], part["uri"], part["part"]) == ("part", "order.g.dart", True)
    assert (part_of["kind"], part_of["uri"], part_of["part"]) == ("part_of", "order.dart", True)


def test_part_of_with_a_library_name_has_no_uri():
    imp = _one("part of shop_app.legacy;\n")

    assert (imp["kind"], imp["uri"], imp["library"]) == ("part_of", None, "shop_app.legacy")


@pytest.mark.parametrize(
    "source",
    [
        "// import 'commented.dart';\n",
        "/* import 'commented.dart'; */\n",
        "const s = \"import 'in_string.dart';\";\n",
        "void f() {\n  final import = 1;\n  print('part of nothing');\n}\n",
    ],
)
def test_text_that_only_looks_like_a_directive_is_ignored(source):
    assert _imports(source) == []


def test_type_declarations_become_symbols():
    source = (
        "class Order {\n  final int id;\n  void save() {}\n}\n"
        "abstract class Repo<T> {}\n"
        "sealed class Result {}\n"
        "final class Token {}\n"
        "mixin Named on Object {}\n"
        "enum Status { open, closed }\n"
        "extension OrderX on Order {\n  int get twice => id * 2;\n}\n"
        "extension type Meters(double value) {}\n"
        "typedef OrderId = int;\n"
        "typedef Compare<T> = int Function(T a, T b);\n"
        "class _Private {}\n"
    )

    assert _symbols(source) == [
        ("Order", "class", 1, True),
        ("Repo", "class", 5, True),
        ("Result", "class", 6, True),
        ("Token", "class", 7, True),
        ("Named", "mixin", 8, True),
        ("Status", "enum", 9, True),
        ("OrderX", "extension", 10, True),
        ("Meters", "extension", 13, True),
        ("OrderId", "typedef", 14, True),
        ("Compare", "typedef", 15, True),
        ("_Private", "class", 16, False),
    ]


def test_top_level_functions_getters_and_variables_become_symbols():
    source = (
        "import 'a.dart';\n"
        "void main() {\n  runApp(const App());\n}\n"
        "Future<Order> fetchOrder(int id) async {\n  return load(id);\n}\n"
        "String label() => 'x';\n"
        "T first<T>(List<T> items) => items.first;\n"
        "int get orderCount => 0;\n"
        "set orderCount(int value) {}\n"
        "final orderServiceProvider = Provider<OrderService>((ref) {\n  return OrderService();\n});\n"
        "const _limit = 10;\n"
        "late final Config config;\n"
        "final Map<String, int> cache = {};\n"
        "var counter = 0;\n"
        "String? currentUser;\n"
        "untyped() {}\n"
    )

    assert _symbols(source) == [
        ("main", "function", 2, True),
        ("fetchOrder", "function", 5, True),
        ("label", "function", 8, True),
        ("first", "function", 9, True),
        ("orderCount", "function", 10, True),
        ("orderServiceProvider", "variable", 12, True),
        ("_limit", "variable", 15, False),
        ("config", "variable", 16, True),
        ("cache", "variable", 17, True),
        ("counter", "variable", 18, True),
        ("currentUser", "variable", 19, True),
        ("untyped", "function", 20, True),
    ]


def test_annotations_before_a_declaration_are_skipped():
    source = (
        "@riverpod\n"
        "Future<int> total(Ref ref) async => 1;\n"
        "@JsonSerializable(explicitToJson: true)\n"
        "@immutable\n"
        "class Model {}\n"
        "@Deprecated('use other')\n"
        "void old() {}\n"
    )

    assert [(name, line) for name, _kind, line, _exported in _symbols(source)] == [
        ("total", 2), ("Model", 5), ("old", 7)]


def test_long_multi_line_annotation_does_not_hide_its_declaration():
    tables = ", ".join("Table%d" % index for index in range(120))
    source = "@DriftDatabase(\n  tables: [%s],\n)\nclass AppDatabase extends _$AppDatabase {}\n" % tables

    assert _symbols(source) == [("AppDatabase", "class", 4, True)]


def test_members_and_continuation_lines_are_not_top_level_symbols():
    source = (
        "class A {\n  int value = 0;\n  void method() {}\n  static A create() => A();\n}\n"
        "final handler =\n    buildHandler(1);\n"
        "final void Function(int) callback = print;\n"
        "extension on String {\n  bool get blank => isEmpty;\n}\n"
    )

    assert [name for name, _kind, _line, _exported in _symbols(source)] == ["A", "handler"]


def test_unbalanced_file_is_degraded_but_keeps_its_directives():
    result = dart_extract.extract("lib/m.dart", "import 'a.dart';\nclass A {\n")

    assert result["degraded"] is True
    assert result["symbols"] == []
    assert [i["uri"] for i in result["imports"]] == ["a.dart"]
