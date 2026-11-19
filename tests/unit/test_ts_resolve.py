import json

import pytest

from arche_lib.langs import ts_resolve

CODE = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")


def _ctx(files):
    """`files` maps each path to its text; code files may map to an empty string."""
    parsed = [path for path in files if path.endswith(CODE)]
    return ts_resolve.build_context(parsed, list(files), files.get)


def _imp(spec, names=()):
    return {"kind": "import", "spec": spec, "names": list(names), "attrs": {}}


def _resolve(spec, importer, files, names=()):
    return ts_resolve.resolve(_imp(spec, names), importer, _ctx(files))


def _verified(to, names=()):
    return [{"status": "verified", "to": to, "names": list(names)}]


def _unresolved(spec, reason):
    return [{"status": "unresolved", "spec": spec, "reason": reason}]


def _tsconfig(**options):
    return json.dumps({"compilerOptions": options})


@pytest.mark.parametrize(
    "spec, target",
    [
        ("./util", "src/util.ts"),
        ("./view", "src/view.tsx"),
        ("./lib", "src/lib/index.ts"),
        ("./service.js", "src/service.ts"),
        ("./legacy.js", "src/legacy.js"),
        ("../shared/types", "shared/types.d.ts"),
    ],
)
def test_relative_specifiers_find_files_by_extension_index_and_js_to_ts(spec, target):
    files = dict.fromkeys(
        ["src/main.ts", "src/util.ts", "src/view.tsx", "src/lib/index.ts", "src/service.ts",
         "src/legacy.js", "shared/types.d.ts"], "")

    assert _resolve(spec, "src/main.ts", files, ["x"]) == _verified(target, ["x"])


def test_file_wins_over_a_directory_index_with_the_same_name():
    files = dict.fromkeys(["src/main.ts", "src/api.ts", "src/api/index.ts"], "")

    assert _resolve("./api", "src/main.ts", files) == _verified("src/api.ts")


def test_js_specifier_prefers_the_typescript_source_over_emitted_javascript():
    files = dict.fromkeys(["src/main.ts", "src/contracts.ts", "src/contracts.js"], "")

    assert _resolve("./contracts.js", "src/main.ts", files) == _verified("src/contracts.ts")


def test_relative_import_of_a_non_code_file_is_target_skipped():
    files = {"src/main.ts": "", "src/data.json": "{}", "src/style.css": ""}

    assert _resolve("./data.json", "src/main.ts", files) == _unresolved("./data.json", "target_skipped")
    assert _resolve("./style.css", "src/main.ts", files) == _unresolved("./style.css", "target_skipped")


def test_relative_import_that_is_missing_or_leaves_the_repository():
    files = {"src/main.ts": ""}

    assert _resolve("./nope", "src/main.ts", files) == _unresolved("./nope", "module_not_found")
    assert _resolve("../../x", "src/main.ts", files) == _unresolved("../../x", "outside_root")


@pytest.mark.parametrize(
    "spec, package",
    [
        ("react", "react"),
        ("react-dom/server", "react-dom"),
        ("@hono/zod-openapi", "@hono/zod-openapi"),
        ("@scope/pkg/deep/file", "@scope/pkg"),
        ("node:fs", "node:fs"),
        ("npm:hono@4.6.3/cors", "npm:hono"),
        ("jsr:@std/assert@1", "jsr:@std/assert"),
        ("https://deno.land/std/path/mod.ts", "https://deno.land/std/path/mod.ts"),
    ],
)
def test_packages_and_protocol_specifiers_are_external(spec, package):
    assert _resolve(spec, "src/main.ts", {"src/main.ts": ""}) == [{"status": "external", "package": package}]


def test_subpath_and_absolute_specifiers_are_unresolved():
    files = {"src/main.ts": ""}

    assert _resolve("#internal/x", "src/main.ts", files) == _unresolved("#internal/x", "subpath_import")
    assert _resolve("/src/x", "src/main.ts", files) == _unresolved("/src/x", "absolute_path")


def test_dynamic_import_without_a_literal_is_unresolved():
    assert _resolve(None, "src/main.ts", {"src/main.ts": ""}) == _unresolved("<dynamic>", "dynamic")


def test_paths_alias_without_base_url_is_relative_to_the_config():
    files = {
        "app/tsconfig.json": _tsconfig(paths={"@/*": ["./src/*"]}),
        "app/src/main.ts": "", "app/src/lib/math.ts": "",
    }

    assert _resolve("@/lib/math", "app/src/main.ts", files) == _verified("app/src/lib/math.ts")


def test_paths_alias_with_base_url_and_an_exact_pattern():
    files = {
        "tsconfig.json": _tsconfig(baseUrl="./src", paths={"@config": ["config/index.ts"], "@lib/*": ["lib/*"]}),
        "src/main.ts": "", "src/config/index.ts": "", "src/lib/a.ts": "",
    }

    assert _resolve("@config", "src/main.ts", files) == _verified("src/config/index.ts")
    assert _resolve("@lib/a", "src/main.ts", files) == _verified("src/lib/a.ts")


def test_alias_that_matches_a_pattern_but_no_file_is_alias_no_match():
    files = {"tsconfig.json": _tsconfig(paths={"@lib/*": ["src/lib/*"]}), "src/main.ts": ""}

    assert _resolve("@lib/nope", "src/main.ts", files) == _unresolved("@lib/nope", "alias_no_match")


def test_catch_all_pattern_lets_unmatched_packages_stay_external():
    files = {"tsconfig.json": _tsconfig(paths={"*": ["types/*"]}), "src/main.ts": "", "types/local.d.ts": ""}

    assert _resolve("local", "src/main.ts", files) == _verified("types/local.d.ts")
    assert _resolve("react", "src/main.ts", files) == [{"status": "external", "package": "react"}]


def test_the_most_specific_pattern_wins():
    files = {
        "tsconfig.json": _tsconfig(paths={"@/*": ["src/*"], "@/gen/*": ["generated/*"]}),
        "src/main.ts": "", "src/gen/a.ts": "", "generated/a.ts": "",
    }

    assert _resolve("@/gen/a", "src/main.ts", files) == _verified("generated/a.ts")


def test_base_url_resolves_bare_paths():
    files = {"tsconfig.json": _tsconfig(baseUrl="."), "src/main.ts": "", "src/util/helper.ts": ""}

    assert _resolve("src/util/helper", "src/main.ts", files) == _verified("src/util/helper.ts")


def test_config_with_comments_and_trailing_commas_is_read():
    config = '{\n  // aliases\n  "compilerOptions": {"paths": {"@/*": ["./src/*"],},},\n}\n'
    files = {"tsconfig.json": config, "src/main.ts": "", "src/a.ts": ""}

    assert _resolve("@/a", "src/main.ts", files) == _verified("src/a.ts")


def test_relative_extends_inherits_paths_anchored_at_the_base_config():
    files = {
        "tsconfig.base.json": _tsconfig(baseUrl=".", paths={"@lib/*": ["shared/lib/*"]}),
        "apps/web/tsconfig.json": json.dumps({"extends": "../../tsconfig.base.json"}),
        "apps/web/src/main.ts": "", "shared/lib/a.ts": "",
    }

    assert _resolve("@lib/a", "apps/web/src/main.ts", files) == _verified("shared/lib/a.ts")


def test_project_references_supply_paths_when_the_root_config_has_none():
    files = {
        "tsconfig.json": json.dumps({"files": [], "references": [{"path": "./tsconfig.app.json"}]}),
        "tsconfig.app.json": _tsconfig(paths={"@/*": ["./src/*"]}),
        "src/main.ts": "", "src/a.ts": "",
    }

    assert _resolve("@/a", "src/main.ts", files) == _verified("src/a.ts")


def test_the_nearest_config_above_the_importer_applies():
    files = {
        "tsconfig.json": _tsconfig(paths={"@/*": ["./root/*"]}),
        "apps/web/tsconfig.json": _tsconfig(paths={"@/*": ["./src/*"]}),
        "apps/web/src/main.ts": "", "apps/web/src/a.ts": "", "root/a.ts": "",
    }

    assert _resolve("@/a", "apps/web/src/main.ts", files) == _verified("apps/web/src/a.ts")


def test_workspace_package_entry_named_by_its_manifest_is_verified():
    manifest = {"name": "@acme/shared",
                "exports": {".": {"types": "./src/index.ts", "default": "./dist/index.js"},
                            "./deep": "./src/deep.ts"}}
    files = {"packages/shared/package.json": json.dumps(manifest), "packages/shared/src/index.ts": "",
             "packages/shared/src/deep.ts": "", "apps/web/src/main.ts": ""}

    assert _resolve("@acme/shared", "apps/web/src/main.ts", files, ["x"]) == _verified(
        "packages/shared/src/index.ts", ["x"])
    assert _resolve("@acme/shared/deep", "apps/web/src/main.ts", files) == _verified(
        "packages/shared/src/deep.ts")


def test_workspace_package_found_only_by_convention_is_inferred():
    files = {"packages/legacy/package.json": json.dumps({"name": "@acme/legacy", "main": "dist/index.js"}),
             "packages/legacy/src/index.ts": "", "packages/legacy/src/extra.ts": "", "src/main.ts": ""}

    assert _resolve("@acme/legacy", "src/main.ts", files) == [
        {"status": "inferred", "to": "packages/legacy/src/index.ts", "names": [],
         "rule": "workspace-source-fallback"}
    ]
    assert _resolve("@acme/legacy/extra", "src/main.ts", files)[0]["to"] == "packages/legacy/src/extra.ts"


def test_workspace_package_without_a_findable_entry_is_unresolved():
    files = {"packages/built/package.json": json.dumps({"name": "built", "main": "dist/index.js"}),
             "src/main.ts": ""}

    assert _resolve("built", "src/main.ts", files) == _unresolved("built", "workspace_package")


def test_manifests_inside_node_modules_do_not_define_workspace_packages():
    files = {"node_modules/react/package.json": json.dumps({"name": "react", "main": "index.js"}),
             "node_modules/react/index.js": "", "src/main.ts": ""}

    assert _resolve("react", "src/main.ts", files) == [{"status": "external", "package": "react"}]


def test_typescript_and_javascript_files_share_one_context():
    files = dict.fromkeys(["src/main.ts", "src/old.js", "src/new.ts", "src/caller.js"], "")

    assert _resolve("./old", "src/main.ts", files) == _verified("src/old.js")
    assert _resolve("./new", "src/caller.js", files) == _verified("src/new.ts")
