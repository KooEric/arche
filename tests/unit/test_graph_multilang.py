import hashlib

from arche_lib.graph import build_graph
from arche_lib.inventory import count_lines
from arche_lib.langs import language_for


def _graph(sources, extra_files=()):
    files = [
        {"path": path, "status": "analyzed", "lang": language_for(path), "bytes": len(text),
         "lines": count_lines(text), "sha256": hashlib.sha256(text.encode()).hexdigest()}
        for path, text in sources.items() if language_for(path)
    ]
    others = [
        {"path": path, "status": "skipped", "reason": "unsupported_lang", "bytes": len(text),
         "lines": count_lines(text), "sha256": hashlib.sha256(text.encode()).hexdigest()}
        for path, text in sources.items() if not language_for(path)
    ]
    inventory = {
        "schema_version": "0.1",
        "repo": {"name": "demo", "root": "/abs/demo", "commit": None, "dirty": None},
        "files": files + others + list(extra_files),
    }
    return build_graph(inventory, sources.get)


def _edge(graph, importer, target):
    return next(e for e in graph["edges"] if e["from"] == "file:" + importer and e["to"] == "file:" + target)


def test_typescript_and_javascript_resolve_into_each_other():
    graph = _graph({
        "src/main.ts": "import { old } from './old';\n",
        "src/old.js": "import { fresh } from './fresh';\nexport const old = fresh;\n",
        "src/fresh.ts": "export const fresh = 1;\n",
    })

    assert _edge(graph, "src/main.ts", "src/old.js")["status"] == "verified"
    assert _edge(graph, "src/old.js", "src/fresh.ts")["status"] == "verified"
    assert {n["lang"] for n in graph["nodes"] if n["type"] == "file"} == {"typescript", "javascript"}


def test_three_languages_build_one_graph():
    graph = _graph({
        "pubspec.yaml": "name: demo_app\n",
        "lib/main.dart": "import 'package:demo_app/util.dart';\nvoid main() {}\n",
        "lib/util.dart": "int twice(int v) => v * 2;\n",
        "web/app.ts": "import { helper } from './helper';\nexport const app = helper;\n",
        "web/helper.ts": "export const helper = 1;\n",
        "tools/run.py": "import helpers\n",
        "tools/helpers.py": "VALUE = 1\n",
    })

    verified = {(e["from"], e["to"]) for e in graph["edges"] if e["status"] == "verified"}

    assert verified == {
        ("file:lib/main.dart", "file:lib/util.dart"),
        ("file:web/app.ts", "file:web/helper.ts"),
        ("file:tools/run.py", "file:tools/helpers.py"),
    }
    assert "sym:lib/util.dart#twice" in {n["id"] for n in graph["nodes"]}


def test_typescript_edge_carries_aliases_prefix_and_reexport():
    graph = _graph({
        "src/main.ts": (
            "import Logger, { level as lvl } from './logger';\n"
            "import * as ns from './logger';\n"
            "export * from './logger';\n"
        ),
        "src/logger.ts": "export default class Logger {}\nexport const level = 1;\n",
    })
    first, second, third = [e for e in graph["edges"] if e["from"] == "file:src/main.ts"]

    assert (first["names"], first["aliases"]) == (["default", "level"], {"Logger": "default", "lvl": "level"})
    assert (second["names"], second["prefix"]) == (["*"], "ns")
    assert third["reexport"] is True
    assert "reexport" not in first


def test_dart_edge_carries_prefix_hide_part_and_conditional():
    graph = _graph({
        "lib/a.dart": (
            "import 'b.dart' as b hide secret;\n"
            "import 'stub.dart' if (dart.library.io) 'io.dart';\n"
            "part 'a_part.dart';\n"
        ),
        "lib/b.dart": "const secret = 1;\n",
        "lib/stub.dart": "",
        "lib/io.dart": "",
        "lib/a_part.dart": "part of 'a.dart';\n",
    })

    imported = _edge(graph, "lib/a.dart", "lib/b.dart")
    assert (imported["prefix"], imported["hide"]) == ("b", ["secret"])
    assert "conditional" not in _edge(graph, "lib/a.dart", "lib/stub.dart")
    assert _edge(graph, "lib/a.dart", "lib/io.dart")["conditional"] is True
    assert _edge(graph, "lib/a.dart", "lib/a_part.dart")["part"] is True
    assert _edge(graph, "lib/a_part.dart", "lib/a.dart")["part"] is True


def test_unbalanced_file_is_marked_degraded_on_its_node():
    graph = _graph({
        "src/ok.ts": "export const ok = 1;\n",
        "src/broken.ts": "import { ok } from './ok';\nexport function f() {\n",
    })
    nodes = {n["id"]: n for n in graph["nodes"]}

    assert nodes["file:src/broken.ts"]["degraded"] is True
    assert "degraded" not in nodes["file:src/ok.ts"]
    assert "sym:src/broken.ts#f" not in nodes
    assert _edge(graph, "src/broken.ts", "src/ok.ts")["status"] == "verified"


def test_sql_and_hql_files_are_nodes_with_no_symbols_or_edges():
    graph = _graph({"q/load.sql": "select 1\n", "q/settle.hql": "select 2\n"})

    assert [(n["id"], n["type"], n["lang"]) for n in graph["nodes"]] == [
        ("file:q/load.sql", "file", "sql"), ("file:q/settle.hql", "file", "sql")]
    assert graph["edges"] == [] and graph["skipped"] == []
