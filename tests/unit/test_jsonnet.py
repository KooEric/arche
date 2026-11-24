import hashlib
import subprocess
import sys
from pathlib import Path

import brief
from arche_lib.graph import build_graph
from arche_lib.inventory import count_lines
from arche_lib.langs import jsonnet, language_for

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "arche" / "scripts"


def _specs(text):
    return [(imp["kind"], imp["spec"], imp["line"]) for imp in jsonnet.extract("a.jsonnet", text)["imports"]]


def _ctx(paths, jpath=()):
    parsed = [p for p in paths if language_for(p) == "jsonnet"]
    return jsonnet.build_context(parsed, paths, lambda rel: None, jpath=jpath)


def _resolve(spec, importer, ctx):
    return jsonnet.resolve({"kind": "import", "spec": spec, "names": [], "line": 1, "quote": ""}, importer, ctx)


def test_language_for_jsonnet_and_libsonnet():
    assert language_for("dash/main.jsonnet") == "jsonnet"
    assert language_for("lib/util.libsonnet") == "jsonnet"


def test_reads_all_three_import_kinds_with_their_lines():
    text = 'local a = import "a.libsonnet";\nlocal b = importstr \'b.txt\';\n{ c: importbin @"c.bin" }\n'

    assert _specs(text) == [("import", "a.libsonnet", 1), ("importstr", "b.txt", 2), ("importbin", "c.bin", 3)]


def test_imports_inside_comments_strings_and_text_blocks_are_not_read():
    text = (
        '// import "line.libsonnet"\n'
        '# import "hash.libsonnet"\n'
        '/* import "block.libsonnet" */\n'
        'local s = "import \\"quoted.libsonnet\\"";\n'
        'local t = |||\n'
        '  import "text.libsonnet"\n'
        '|||;\n'
        'import "real.libsonnet"\n'
    )

    assert _specs(text) == [("import", "real.libsonnet", 8)]


def test_verbatim_string_doubles_its_quote_and_escapes_are_decoded():
    assert _specs("import @'it''s.libsonnet'\nimport \"a\\\\b.libsonnet\"\n") == [
        ("import", "it's.libsonnet", 1), ("import", "a\\b.libsonnet", 2)]


def test_import_of_a_non_literal_is_skipped():
    assert _specs("local f = 'x'; import f\n") == []


def test_unterminated_string_marks_the_file_degraded_and_keeps_earlier_imports():
    result = jsonnet.extract("a.jsonnet", 'import "ok.libsonnet"\nlocal x = "never ends\n')

    assert result["degraded"] is True
    assert [imp["spec"] for imp in result["imports"]] == ["ok.libsonnet"]


def test_top_level_locals_are_symbols_but_nested_ones_are_not():
    text = "local a = 1, f(x) = x;\n{ local hidden = 2, field: hidden }\n"

    assert [(s["name"], s["line"]) for s in jsonnet.extract("a.jsonnet", text)["symbols"]] == [("a", 1), ("f", 1)]


def test_relative_import_is_verified():
    ctx = _ctx(["dash/main.jsonnet", "dash/panels.libsonnet", "lib/panels.libsonnet"])

    assert _resolve("panels.libsonnet", "dash/main.jsonnet", ctx) == [
        {"status": "verified", "to": "dash/panels.libsonnet", "names": [], "rule": "relative"}]
    assert _resolve("../lib/panels.libsonnet", "dash/main.jsonnet", ctx) == [
        {"status": "verified", "to": "lib/panels.libsonnet", "names": [], "rule": "relative"}]


def test_jpath_is_searched_right_most_first_and_is_verified():
    ctx = _ctx(["dash/main.jsonnet", "libs/a/util.libsonnet", "libs/b/util.libsonnet"], jpath=["libs/a", "libs/b"])

    assert _resolve("util.libsonnet", "dash/main.jsonnet", ctx) == [
        {"status": "verified", "to": "libs/b/util.libsonnet", "names": [], "rule": "jpath"}]


def test_without_jpath_a_single_lib_directory_is_an_inferred_guess():
    ctx = _ctx(["dash/main.jsonnet", "lib/util.libsonnet"])

    assert _resolve("util.libsonnet", "dash/main.jsonnet", ctx) == [
        {"status": "inferred", "to": "lib/util.libsonnet", "names": [], "rule": "jpath_guess"}]


def test_without_jpath_several_lib_directories_are_ambiguous():
    ctx = _ctx(["dash/main.jsonnet", "a/lib/util.libsonnet", "b/lib/util.libsonnet"])

    assert _resolve("util.libsonnet", "dash/main.jsonnet", ctx) == [
        {"status": "unresolved", "spec": "util.libsonnet", "reason": "ambiguous",
         "candidates": ["a/lib/util.libsonnet", "b/lib/util.libsonnet"]}]


def test_given_jpath_turns_off_the_lib_guess():
    ctx = _ctx(["dash/main.jsonnet", "lib/util.libsonnet", "other/x.libsonnet"], jpath=["other"])

    assert _resolve("util.libsonnet", "dash/main.jsonnet", ctx) == [
        {"status": "unresolved", "spec": "util.libsonnet", "reason": "module_not_found"}]


def test_bundler_packages_are_external():
    ctx = _ctx(["jsonnetfile.json", "dash/main.jsonnet"])

    assert _resolve("github.com/grafana/grafonnet/gen/grafonnet-latest/main.libsonnet", "dash/main.jsonnet", ctx) == [
        {"status": "external", "package": "github.com/grafana/grafonnet"}]
    assert _resolve("ksonnet-util/kausal.libsonnet", "dash/main.jsonnet", ctx) == [
        {"status": "external", "package": "ksonnet-util"}]


def test_missing_relative_import_is_unresolved_even_with_a_bundler_file():
    ctx = _ctx(["jsonnetfile.json", "dash/main.jsonnet"])

    assert _resolve("./missing.libsonnet", "dash/main.jsonnet", ctx) == [
        {"status": "unresolved", "spec": "./missing.libsonnet", "reason": "module_not_found"}]


def test_import_of_an_unparsed_file_is_target_skipped():
    ctx = _ctx(["dash/main.jsonnet", "dash/config.json"])

    assert _resolve("config.json", "dash/main.jsonnet", ctx) == [
        {"status": "unresolved", "spec": "config.json", "reason": "target_skipped"}]


def test_import_above_the_repository_root_is_not_followed():
    ctx = _ctx(["main.jsonnet", "x.libsonnet"])

    assert _resolve("../x.libsonnet", "main.jsonnet", ctx) == [
        {"status": "unresolved", "spec": "../x.libsonnet", "reason": "module_not_found"}]


def _graph(sources, options=None):
    files = [
        {"path": path, "status": "analyzed" if language_for(path) else "skipped",
         **({"lang": language_for(path)} if language_for(path) else {"reason": "unsupported_lang"}),
         "bytes": len(text), "lines": count_lines(text), "sha256": hashlib.sha256(text.encode()).hexdigest()}
        for path, text in sources.items()
    ]
    inventory = {"schema_version": "0.1", "repo": {"name": "demo", "root": "/abs/demo", "commit": None, "dirty": None},
                 "files": files}
    return build_graph(inventory, sources.get, options)


SOURCES = {
    "dash/main.jsonnet": 'local g = import "grafana.libsonnet";\ng\n',
    "vendor-libs/grafana.libsonnet": "{}\n",
    "Makefile": "build:\n\tjsonnet -J vendor-libs dash/main.jsonnet\n",
}


def test_graph_passes_jpath_to_the_adapter_and_records_it():
    graph = _graph(SOURCES, {"jpath": ["vendor-libs"]})

    edge = next(e for e in graph["edges"] if e["from"] == "file:dash/main.jsonnet")
    assert (edge["kind"], edge["status"], edge["rule"], edge["to"]) == (
        "IMPORTS", "verified", "jpath", "file:vendor-libs/grafana.libsonnet")
    assert graph["options"] == {"jpath": ["vendor-libs"]}


def test_graph_without_options_has_no_options_key():
    graph = _graph(SOURCES)

    assert "options" not in graph
    edge = next(e for e in graph["edges"] if e["from"] == "file:dash/main.jsonnet")
    assert (edge["status"], edge["reason"]) == ("unresolved", "module_not_found")


def test_brief_lists_lines_that_pass_library_directories():
    graph = _graph(SOURCES)

    text = brief.build(graph, SOURCES.get, list(SOURCES))

    assert "JSONNET LIBRARY PATHS (--jpath given: none)" in text
    assert "Makefile:2" in text


def test_imports_cli_rejects_a_jpath_outside_the_repository(tmp_path):
    for bad in ("/abs/lib", "../lib", "a/../../lib"):
        done = subprocess.run([sys.executable, "-B", str(SCRIPTS / "imports.py"), "--out", str(tmp_path), "--jpath", bad],
                              capture_output=True, text=True)
        assert done.returncode != 0
        assert "--jpath must be a directory inside the repository" in done.stderr
