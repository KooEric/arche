import pytest

from arche_lib.langs import ts_extract


def _imports(source):
    return ts_extract.extract("m.ts", source)["imports"]


def _one(source):
    found = _imports(source)
    assert len(found) == 1, found
    return found[0]


def _symbols(source):
    return [(s["name"], s["kind"], s["line"], s["exported"])
            for s in ts_extract.extract("m.ts", source)["symbols"]]


def test_default_import_records_the_local_alias():
    imp = _one("import React from 'react';\n")

    assert (imp["kind"], imp["spec"], imp["names"]) == ("import", "react", ["default"])
    assert imp["attrs"] == {"aliases": {"React": "default"}}
    assert (imp["line"], imp["quote"]) == (1, "import React from 'react';")


def test_named_imports_with_aliases_and_inline_type_modifiers():
    imp = _one('import { a, b as c, type D, default as E } from "./mod";\n')

    assert imp["names"] == ["a", "b", "D", "default"]
    assert imp["attrs"] == {"aliases": {"c": "b", "E": "default"}}
    assert imp["type_only"] is False


def test_namespace_import_records_a_prefix():
    imp = _one("import * as ns from './mod';\n")

    assert imp["names"] == ["*"]
    assert imp["attrs"] == {"prefix": "ns"}


def test_default_and_named_imports_in_one_statement_spanning_lines():
    imp = _one("import Logger, {\n  level,\n  log,\n} from './logger';\n")

    assert imp["names"] == ["default", "level", "log"]
    assert (imp["line"], imp["quote"]) == (1, "import Logger, {")


def test_side_effect_import_has_no_names():
    imp = _one("import './setup';\n")

    assert (imp["spec"], imp["names"], imp["attrs"]) == ("./setup", [], {})


def test_import_type_is_type_only():
    imp = _one("import type { User } from './user';\n")

    assert (imp["names"], imp["type_only"]) == (["User"], True)


@pytest.mark.parametrize(
    "source, names",
    [
        ("export * from './a';\n", ["*"]),
        ("export * as ns from './a';\n", ["*"]),
        ("export { x, y as z } from './a';\n", ["x", "y"]),
        ("export type { T } from './a';\n", ["T"]),
    ],
)
def test_export_from_is_a_reexport(source, names):
    imp = _one(source)

    assert (imp["kind"], imp["spec"], imp["names"], imp["reexport"]) == ("export", "./a", names, True)


def test_local_export_list_is_not_an_import():
    assert _imports("const a = 1;\nexport { a };\nexport default a;\n") == []


def test_require_with_a_literal_is_resolvable_and_conditional_only_when_nested():
    top, nested = _imports("const a = require('./a');\nfunction f() { return require('./b'); }\n")

    assert (top["kind"], top["spec"], top["dynamic"], top["conditional"]) == ("require", "./a", False, False)
    assert (nested["spec"], nested["conditional"]) == ("./b", True)


@pytest.mark.parametrize(
    "source",
    ["const m = require(name);\n", "const m = require('./plugins/' + id);\n",
     "const m = import(`./plugins/${name}`);\n", "const m = import(path);\n"],
)
def test_non_literal_calls_have_no_spec_and_are_dynamic(source):
    imp = _one(source)

    assert (imp["spec"], imp["dynamic"]) == (None, True)


@pytest.mark.parametrize("source", ["const m = import('./a');\n", 'const m = () => import("./a");\n',
                                    "const m = import(`./a`);\n"])
def test_dynamic_import_with_a_literal_keeps_the_spec(source):
    imp = _one(source)

    assert (imp["kind"], imp["spec"], imp["dynamic"]) == ("dynamic", "./a", True)


@pytest.mark.parametrize(
    "source",
    [
        "// import x from './commented';\n",
        "/* import('./commented') */\n",
        "const s = \"import('./in-string')\";\n",
        "const re = /import\\(['\"]x['\"]\\)/;\n",
        "const url = import.meta.url;\n",
        "loader.import('./member');\nloader.require('./member');\n",
        "const { import: imp, require: req } = tools;\n",
    ],
)
def test_text_that_only_looks_like_an_import_is_ignored(source):
    assert _imports(source) == []


def test_records_come_back_in_source_order():
    source = "const a = require('./a');\nimport b from './b';\nexport * from './c';\n"

    assert [i["spec"] for i in _imports(source)] == ["./a", "./b", "./c"]


def test_top_level_declarations_become_symbols():
    source = (
        "import x from './x';\n"
        "export const PORT = 3000;\n"
        "let counter = 0;\n"
        "export function start() {\n  function inner() {}\n  const local = 1;\n}\n"
        "export async function load() {}\n"
        "function* gen() {}\n"
        "export class Server {\n  method() {}\n}\n"
        "export interface Options {}\n"
        "export type Id = string;\n"
        "export enum Role { A }\n"
        "declare const VERSION: string;\n"
        "export abstract class Base {}\n"
    )

    assert _symbols(source) == [
        ("PORT", "variable", 2, True),
        ("counter", "variable", 3, False),
        ("start", "function", 4, True),
        ("load", "function", 8, True),
        ("gen", "function", 9, False),
        ("Server", "class", 10, True),
        ("Options", "type", 13, True),
        ("Id", "type", 14, True),
        ("Role", "enum", 15, True),
        ("VERSION", "variable", 16, False),
        ("Base", "class", 17, True),
    ]


@pytest.mark.parametrize(
    "source, expected",
    [
        ("export default function handler() {}\n",
         [("handler", "function", 1, True), ("default", "default", 1, True)]),
        ("export default class extends Base {}\n", [("default", "default", 1, True)]),
        ("const App = () => null;\nexport default App;\n",
         [("App", "variable", 1, False), ("default", "default", 2, True)]),
        ("export default { port: 1 };\n", [("default", "default", 1, True)]),
    ],
)
def test_default_exports_add_a_default_symbol(source, expected):
    assert _symbols(source) == expected


def test_overloads_and_destructuring_do_not_duplicate_or_invent_symbols():
    source = (
        "export function f(a: string): void;\n"
        "export function f(a: number): void;\n"
        "export function f(a: unknown): void {}\n"
        "const { a, b } = load();\n"
        "const [c] = list;\n"
    )

    assert _symbols(source) == [("f", "function", 1, True)]


def test_callback_function_inside_a_call_is_not_top_level():
    source = "app.get('/x', function handler(req, res) {\n  res.end();\n});\nconst after = 1;\n"

    assert _symbols(source) == [("after", "variable", 4, False)]


def test_jsx_component_file_keeps_its_symbols():
    source = (
        "import React from 'react';\n"
        "export const Button = () => <button className=\"b\">Don't click</button>;\n"
        "export default Button;\n"
    )

    result = ts_extract.extract("Button.tsx", source)

    assert "degraded" not in result
    assert [s["name"] for s in result["symbols"]] == ["Button", "default"]


def test_unbalanced_file_is_degraded_but_keeps_its_imports():
    result = ts_extract.extract("m.ts", "import a from './a';\nexport function f() {\n")

    assert result["degraded"] is True
    assert result["symbols"] == []
    assert [i["spec"] for i in result["imports"]] == ["./a"]
