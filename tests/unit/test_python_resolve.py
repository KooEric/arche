from arche_lib.langs import python_resolve


def _ctx(paths, parsed=None):
    parsed = [p for p in paths if p.endswith(".py")] if parsed is None else parsed
    return python_resolve.build_context(parsed, paths, lambda rel: None)


def _imp(kind, module, names=(), level=0):
    return {"kind": kind, "module": module, "level": level, "names": list(names),
            "line": 1, "quote": "", "conditional": False, "type_only": False, "dynamic": False}


def _verified(to, names=()):
    return {"status": "verified", "to": to, "names": list(names)}


def test_source_roots_include_marker_dirs_and_their_src_folders():
    ctx = _ctx(["pyproject.toml", "src/shop/a.py", "services/api/setup.py", "services/api/app.py"])

    assert ctx.roots == ("", "services/api", "src")


def test_absolute_import_resolves_to_module_file_or_package_init():
    ctx = _ctx(["pkg/__init__.py", "pkg/mod.py", "main.py"])

    assert python_resolve.resolve(_imp("import", "pkg.mod"), "main.py", ctx) == [_verified("pkg/mod.py")]
    assert python_resolve.resolve(_imp("import", "pkg"), "main.py", ctx) == [_verified("pkg/__init__.py")]


def test_absolute_import_finds_packages_under_a_src_root():
    ctx = _ctx(["pyproject.toml", "src/shop/__init__.py", "src/shop/models.py", "scripts/seed.py"])

    found = python_resolve.resolve(_imp("import", "shop.models"), "scripts/seed.py", ctx)

    assert found == [_verified("src/shop/models.py")]


def test_from_import_splits_submodules_from_names_defined_in_the_module():
    ctx = _ctx(["pkg/__init__.py", "pkg/sub.py", "main.py"])

    found = python_resolve.resolve(_imp("from", "pkg", ["sub", "helper"]), "main.py", ctx)

    assert found == [_verified("pkg/sub.py"), _verified("pkg/__init__.py", ["helper"])]


def test_star_import_points_at_the_module_itself():
    ctx = _ctx(["pkg/__init__.py", "main.py"])

    assert python_resolve.resolve(_imp("from", "pkg", ["*"]), "main.py", ctx) == [
        _verified("pkg/__init__.py", ["*"])
    ]


def test_relative_imports_resolve_against_the_importer_package():
    ctx = _ctx(["pkg/__init__.py", "pkg/a.py", "pkg/sub/__init__.py", "pkg/sub/b.py", "pkg/sub/c.py"])

    assert python_resolve.resolve(_imp("from", "", ["c"], level=1), "pkg/sub/b.py", ctx) == [
        _verified("pkg/sub/c.py")
    ]
    assert python_resolve.resolve(_imp("from", "a", ["thing"], level=2), "pkg/sub/b.py", ctx) == [
        _verified("pkg/a.py", ["thing"])
    ]
    assert python_resolve.resolve(_imp("from", "", ["a"], level=2), "pkg/sub/b.py", ctx) == [
        _verified("pkg/a.py")
    ]


def test_relative_import_above_the_repository_root_is_unresolved():
    ctx = _ctx(["a.py"])

    assert python_resolve.resolve(_imp("from", "x", ["y"], level=2), "a.py", ctx) == [
        {"status": "unresolved", "spec": "..x", "reason": "relative_beyond_root"}
    ]


def test_relative_import_of_a_missing_module_is_unresolved():
    ctx = _ctx(["pkg/__init__.py", "pkg/a.py"])

    assert python_resolve.resolve(_imp("from", "missing", ["y"], level=1), "pkg/a.py", ctx) == [
        {"status": "unresolved", "spec": ".missing", "reason": "module_not_found"}
    ]


def test_unknown_top_level_name_is_an_external_package():
    ctx = _ctx(["main.py"])

    assert python_resolve.resolve(_imp("import", "requests.adapters"), "main.py", ctx) == [
        {"status": "external", "package": "requests"}
    ]
    assert python_resolve.resolve(_imp("from", "os", ["path"]), "main.py", ctx) == [
        {"status": "external", "package": "os"}
    ]


def test_known_package_with_a_missing_submodule_is_unresolved_not_external():
    ctx = _ctx(["pkg/__init__.py", "main.py"])

    assert python_resolve.resolve(_imp("import", "pkg.missing"), "main.py", ctx) == [
        {"status": "unresolved", "spec": "pkg.missing", "reason": "module_not_found"}
    ]


def test_import_of_a_directory_without_init_is_a_namespace_package():
    ctx = _ctx(["ns/mod.py", "main.py"])

    assert python_resolve.resolve(_imp("import", "ns"), "main.py", ctx) == [
        {"status": "unresolved", "spec": "ns", "reason": "namespace_package"}
    ]
    assert python_resolve.resolve(_imp("from", "ns", ["mod"]), "main.py", ctx) == [_verified("ns/mod.py")]


def test_target_that_exists_but_was_not_parsed_is_unresolved():
    paths = ["main.py", "generated/schema_pb2.py"]
    ctx = _ctx(paths, parsed=["main.py"])

    assert python_resolve.resolve(_imp("from", "generated", ["schema_pb2"]), "main.py", ctx) == [
        {"status": "unresolved", "spec": "generated.schema_pb2", "reason": "target_skipped"}
    ]


def test_bare_sibling_import_resolves_only_outside_packages():
    script_ctx = _ctx(["scripts/seed.py", "scripts/helpers.py"])
    package_ctx = _ctx(["pkg/__init__.py", "pkg/seed.py", "pkg/helpers.py"])

    assert python_resolve.resolve(_imp("import", "helpers"), "scripts/seed.py", script_ctx) == [
        _verified("scripts/helpers.py")
    ]
    assert python_resolve.resolve(_imp("import", "helpers"), "pkg/seed.py", package_ctx) == [
        {"status": "external", "package": "helpers"}
    ]


def test_nearest_root_wins_when_a_module_name_exists_twice():
    ctx = _ctx(["util.py", "services/api/setup.py", "services/api/util.py",
                "services/api/app/__init__.py", "services/api/app/main.py"])

    found = python_resolve.resolve(_imp("import", "util"), "services/api/app/main.py", ctx)

    assert found == [_verified("services/api/util.py")]


def test_dynamic_import_without_a_literal_is_unresolved():
    ctx = _ctx(["main.py"])

    assert python_resolve.resolve(_imp("dynamic", None), "main.py", ctx) == [
        {"status": "unresolved", "spec": "<dynamic>", "reason": "dynamic"}
    ]


def test_dynamic_import_with_a_literal_resolves_like_a_plain_import():
    ctx = _ctx(["pkg/__init__.py", "pkg/plugin.py", "main.py"])

    assert python_resolve.resolve(_imp("dynamic", "pkg.plugin"), "main.py", ctx) == [
        _verified("pkg/plugin.py")
    ]


def test_name_found_in_exactly_one_non_package_directory_is_inferred():
    ctx = _ctx(["requirements.txt", "pipeline/run.py", "crawling/card_crawl.py"])

    assert python_resolve.resolve(_imp("import", "card_crawl"), "pipeline/run.py", ctx) == [
        {"status": "inferred", "to": "crawling/card_crawl.py", "names": [],
         "rule": "unique-module-name"}
    ]


def test_package_under_a_non_package_directory_is_inferred_from_inside_itself():
    ctx = _ctx(["tools/demo_web/__init__.py", "tools/demo_web/blog.py", "tools/demo_web/site.py"])

    found = python_resolve.resolve(_imp("from", "demo_web", ["site"]), "tools/demo_web/blog.py", ctx)

    assert found == [
        {"status": "inferred", "to": "tools/demo_web/site.py", "names": [],
         "rule": "unique-module-name"}
    ]


def test_name_found_in_several_non_package_directories_is_ambiguous():
    ctx = _ctx(["main/run.py", "a/util.py", "b/util.py"])

    assert python_resolve.resolve(_imp("import", "util"), "main/run.py", ctx) == [
        {"status": "unresolved", "spec": "util", "reason": "ambiguous_module",
         "candidates": ["a/util.py", "b/util.py"]}
    ]


def test_same_named_module_inside_a_package_does_not_capture_an_external_import():
    ctx = _ctx(["bench/run.py", "pkg/__init__.py", "pkg/sinks/__init__.py", "pkg/sinks/duckdb.py"])

    assert python_resolve.resolve(_imp("import", "duckdb"), "bench/run.py", ctx) == [
        {"status": "external", "package": "duckdb"}
    ]


def test_workspace_src_namespace_package_is_inferred_without_a_local_marker():
    ctx = _ctx(["packages/cli/src/cli/main.py", "packages/core/src/core/stages.py"])

    assert python_resolve.resolve(_imp("from", "core.stages", ["run"]),
                                  "packages/cli/src/cli/main.py", ctx) == [
        {"status": "inferred", "to": "packages/core/src/core/stages.py",
         "names": ["run"], "rule": "unique-module-name"}
    ]


def test_workspace_src_namespace_candidates_are_ambiguous():
    ctx = _ctx(["app/main.py", "packages/a/src/core/stages.py", "packages/b/src/core/stages.py"])

    assert python_resolve.resolve(_imp("import", "core.stages"), "app/main.py", ctx) == [
        {"status": "unresolved", "spec": "core.stages", "reason": "ambiguous_module",
         "candidates": ["packages/a/src/core", "packages/b/src/core"]}
    ]


def test_missing_workspace_src_submodule_is_not_external():
    ctx = _ctx(["app/main.py", "packages/core/src/core/stages.py"])

    assert python_resolve.resolve(_imp("import", "core.missing"), "app/main.py", ctx) == [
        {"status": "unresolved", "spec": "core.missing", "reason": "module_not_found"}
    ]


def _ctx_with_files(files):
    paths = list(files)
    parsed = [p for p in paths if p.endswith(".py")]
    return python_resolve.build_context(parsed, paths, files.get)


AIRFLOW_DAG_REPO = [
    "team_a/src/airflow/dags/load.py",
    "team_b/src/airflow/dags/export.py",
    "team_a/src/airflow/dags/report.py",
]


def test_dotted_import_no_local_airflow_folder_provides_is_external():
    ctx = _ctx(AIRFLOW_DAG_REPO)

    found = python_resolve.resolve(_imp("from", "airflow.operators.python", ["PythonOperator"]),
                                   "team_a/src/airflow/dags/load.py", ctx)

    assert found == [{"status": "external", "package": "airflow"}]


def test_only_the_airflow_folder_that_provides_the_module_is_inferred():
    ctx = _ctx(AIRFLOW_DAG_REPO)

    found = python_resolve.resolve(_imp("from", "airflow.dags.report", ["run"]),
                                   "team_b/src/airflow/dags/export.py", ctx)

    assert found == [{"status": "inferred", "to": "team_a/src/airflow/dags/report.py",
                      "names": ["run"], "rule": "unique-module-name"}]


def test_declared_requirement_makes_a_shadowed_import_external():
    ctx = _ctx_with_files({
        "requirements-dev.txt": "# pinned\napache-airflow[celery]==2.9.1 ; python_version>'3.8'\n-r base.txt\n",
        "team_a/src/airflow/dags/load.py": "",
        "team_b/src/airflow/dags/export.py": "",
    })

    for imp in (_imp("from", "airflow", ["DAG"]), _imp("import", "airflow"),
                _imp("from", "airflow.dags", ["export"])):
        assert python_resolve.resolve(imp, "team_a/src/airflow/dags/load.py", ctx) == [
            {"status": "external", "package": "airflow"}
        ]


def test_declared_dependency_beats_a_root_level_namespace_folder():
    ctx = _ctx_with_files({
        "pyproject.toml": '[project]\nname = "dags"\ndependencies = [\n  "apache-airflow-providers-google>=10",\n  "PyYAML",\n]\n',
        "airflow/dags/load.py": "",
        "yaml/config.py": "",
    })

    assert python_resolve.resolve(_imp("from", "airflow.operators.python", ["PythonOperator"]),
                                  "airflow/dags/load.py", ctx) == [
        {"status": "external", "package": "airflow"}
    ]
    assert python_resolve.resolve(_imp("import", "yaml"), "airflow/dags/load.py", ctx) == [
        {"status": "external", "package": "yaml"}
    ]


def test_declared_dependency_does_not_hide_a_module_that_exists_locally():
    ctx = _ctx_with_files({
        "setup.cfg": "[options]\ninstall_requires =\n    requests>=2\n    apache-airflow\n",
        "airflow/__init__.py": "",
        "airflow/hooks.py": "",
        "main.py": "",
    })

    assert python_resolve.resolve(_imp("import", "airflow.hooks"), "main.py", ctx) == [
        _verified("airflow/hooks.py")
    ]


def test_poetry_dependencies_are_declared():
    ctx = _ctx_with_files({
        "pyproject.toml": '[tool.poetry.dependencies]\npython = "^3.10"\nrequests = "^2"\n\n[tool.poetry.group.dev.dependencies]\npytest = "*"\n',
        "a/requests/__init__.py": "",
        "b/requests/__init__.py": "",
        "main.py": "",
    })

    assert python_resolve.resolve(_imp("import", "requests"), "main.py", ctx) == [
        {"status": "external", "package": "requests"}
    ]
