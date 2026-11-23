from arche_lib.langs import python_deps


def _declared(files):
    return python_deps.declared_import_names(files, files.get)


def test_distribution_names_map_to_their_import_names():
    assert "airflow" in python_deps.import_names("apache-airflow")
    assert "airflow" in python_deps.import_names("apache-airflow-providers-amazon")
    assert "yaml" in python_deps.import_names("PyYAML")
    assert "google" in python_deps.import_names("google-cloud-bigquery")
    assert "dateutil" in python_deps.import_names("python-dateutil")
    assert "requests" in python_deps.import_names("Requests")


def test_requirements_skip_comments_options_and_urls():
    declared = _declared({
        "deps/requirements-prod.txt": "# comment\n-r base.txt\n--index-url https://x\n"
                                      "https://example.com/pkg.whl\npandas>=2  # data\n"
                                      "mylib @ git+https://example.com/mylib.git\n",
    })

    assert {"pandas", "mylib"} <= declared
    assert "https" not in declared and "r" not in declared


def test_pep621_lists_span_lines_and_include_optional_groups():
    declared = _declared({"pyproject.toml": (
        '[project]\nname = "app"\ndependencies = ["httpx>=0.27", "pydantic"]\n'
        '[project.optional-dependencies]\ntest = [\n  "pytest",\n  "hypothesis[cli]",\n]\n'
        '[tool.ruff]\nline-length = 100\n'
    )})

    assert {"httpx", "pydantic", "pytest", "hypothesis"} <= declared
    assert "app" not in declared and "line_length" not in declared


def test_setup_cfg_install_requires_and_extras():
    declared = _declared({"setup.cfg": (
        "[metadata]\nname = app\n[options]\npackages = find:\ninstall_requires =\n"
        "    click>=8\n    rich\n[options.extras_require]\ndev =\n    black\n"
    )})

    assert {"click", "rich", "black"} <= declared
    assert "app" not in declared and "find" not in declared


def test_unreadable_manifests_are_skipped():
    assert python_deps.declared_import_names(["requirements.txt"], lambda path: None) == frozenset()
