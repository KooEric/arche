import hashlib

from arche_lib import pathref
from arche_lib.graph import build_graph
from arche_lib.inventory import count_lines
from arche_lib.langs import language_for, yaml_config
from arche_lib.langs.yaml_reader import scalars
from arche_lib.report import _config_references, _file_references


def _flat(text):
    documents, degraded = scalars(text)
    return [(key, value) for document in documents for key, value, _ in document], degraded


def test_language_for_yaml():
    assert language_for("compose.yaml") == "yaml"
    assert language_for(".github/workflows/ci.yml") == "yaml"


def test_reader_follows_mappings_sequences_and_flow_collections():
    text = (
        "services:\n"
        "  web:\n"
        "    build:\n"
        "      context: ./web   # trailing comment\n"
        "    volumes: [\"./data:/data\", './conf:/etc']\n"
        "    labels: {tier: front}\n"
        "include:\n"
        "- ./a.yml\n"
        "- path: ./b.yml\n"
    )

    assert _flat(text) == ([
        (("services", "web", "build", "context"), "./web"),
        (("services", "web", "volumes", 0), "./data:/data"),
        (("services", "web", "volumes", 1), "./conf:/etc"),
        (("services", "web", "labels", "tier"), "front"),
        (("include", 0), "./a.yml"),
        (("include", 1, "path"), "./b.yml"),
    ], False)


def test_reader_skips_block_scalars_aliases_and_keeps_hash_inside_words():
    text = (
        "run: |\n"
        "  context: ./not-a-key\n"
        "  - ./not-an-item\n"
        "base: &b ./base\n"
        "copy: *b\n"
        "url: http://x/#frag\n"
        "tagged: !Ref ./tagged\n"
    )

    assert _flat(text) == ([(("base",), "./base"), (("url",), "http://x/#frag"), (("tagged",), "./tagged")], False)


def test_reader_splits_documents_and_counts_items_per_document():
    documents, _ = scalars("- a\n- b\n---\n- c\n")

    assert [[(key, value) for key, value, _ in document] for document in documents] == [
        [((0,), "a"), ((1,), "b")], [((0,), "c")]]


def test_reader_skips_template_lines_and_marks_them_degraded():
    flat, degraded = _flat("{% if x %}\nkey: ./value\n{% endif %}\n")

    assert flat == [(("key",), "./value")]
    assert degraded is True


def test_reader_keeps_values_with_run_time_expressions_without_degrading():
    flat, degraded = _flat("with:\n  node: ${{ matrix.node }}\n")

    assert flat == [(("with", "node"), "${{ matrix.node }}")]
    assert degraded is False


def _refs(path, text):
    result = yaml_config.extract(path, text)
    return [(ref["via"], ref["spec"], ref["base"], ref["target"]) for ref in result["path_refs"]], result


COMPOSE = (
    "services:\n"
    "  web:\n"
    "    build:\n"
    "      context: ./web\n"
    "      dockerfile: Dockerfile.dev\n"
    "    env_file: .env.web\n"
    "    volumes:\n"
    "      - ./data:/var/data\n"
    "      - named:/cache\n"
    "      - ./init.sql:/docker-entrypoint-initdb.d/init.sql\n"
    "  worker:\n"
    "    build: ../worker\n"
    "    extends:\n"
    "      file: common.yml\n"
    "  remote:\n"
    "    build: https://github.com/org/repo.git#main\n"
    "include:\n"
    "  - ./more.yaml\n"
)


def test_compose_paths_and_remote_build_context():
    refs, result = _refs("deploy/compose.yaml", COMPOSE)

    assert refs == [
        ("compose:services.*.build.context", "./web", "deploy", "dir"),
        ("compose:services.*.build.dockerfile", "Dockerfile.dev", "deploy/web", "file"),
        ("compose:services.*.env_file", ".env.web", "deploy", "file"),
        ("compose:services.*.volumes", "./data", "deploy", "any"),
        ("compose:services.*.volumes", "./init.sql", "deploy", "any"),
        ("compose:services.*.build", "../worker", "deploy", "dir"),
        ("compose:services.*.extends.file", "common.yml", "deploy", "file"),
        ("compose:include", "./more.yaml", "deploy", "file"),
    ]
    assert result["config_externals"] == [{"package": "https://github.com/org/repo.git", "line": 16}]
    assert result["file_refs"] == []  # the init.sql volume belongs to the compose schema


WORKFLOW = (
    "jobs:\n"
    "  test:\n"
    "    steps:\n"
    "      - uses: actions/checkout@v4\n"
    "      - uses: ./.github/actions/setup\n"
    "      - uses: docker://alpine:3.19\n"
    "      - uses: ${{ matrix.action }}\n"
    "  reuse:\n"
    "    uses: ./.github/workflows/build.yml\n"
    "  call:\n"
    "    uses: org/shared/.github/workflows/lint.yml@v2\n"
)


def test_actions_local_uses_are_root_relative_and_remote_ones_external():
    refs, result = _refs(".github/workflows/ci.yml", WORKFLOW)

    assert refs == [
        ("actions:jobs.*.steps.uses", "./.github/actions/setup", "", "any"),
        ("actions:jobs.*.uses", "./.github/workflows/build.yml", "", "any"),
    ]
    assert [item["package"] for item in result["config_externals"]] == [
        "actions/checkout", "docker://alpine", "org/shared"]


def test_composite_action_steps_are_read():
    refs, _ = _refs(".github/actions/setup/action.yml", "runs:\n  using: composite\n  steps:\n    - uses: ./.github/actions/cache\n")

    assert refs == [("actions:runs.steps.uses", "./.github/actions/cache", "", "any")]


def test_helm_file_dependencies_and_remote_repositories():
    text = ("dependencies:\n"
            "  - name: common\n    repository: file://../common\n"
            "  - name: redis\n    version: 18.x\n    repository: https://charts.bitnami.com/bitnami\n")
    refs, result = _refs("charts/app/Chart.yaml", text)

    assert refs == [("helm:dependencies.repository", "../common", "charts/app", "dir")]
    assert result["config_externals"] == [{"package": "https://charts.bitnami.com/bitnami/redis", "line": 6}]


def test_helm_templates_are_not_read():
    result = yaml_config.extract("charts/app/templates/deploy.yaml", "spec:\n  image: {{ .Values.image }}\n  sql: q/a.sql\n")

    assert (result["path_refs"], result["file_refs"], result.get("degraded")) == ([], [], None)


def test_any_yaml_value_that_names_a_sql_file_is_a_sql_reference():
    result = yaml_config.extract("dags/team_a/load.yaml", "tasks:\n  extract:\n    sql_file: sql/extract.sql\n    other: sql/{{ ds }}.sql\n")

    assert [(ref["spec"], ref["line"], ref["dynamic"]) for ref in result["file_refs"]] == [
        ("sql/extract.sql", 3, False), ("sql/{{ ds }}.sql", 4, True)]
    assert result["path_refs"] == []


def _index(paths, analyzed=None):
    return pathref.build_index(paths, paths if analyzed is None else analyzed)


def _ref(spec, base="", target="any", markers=()):
    return {"spec": spec, "line": 1, "quote": "", "via": "x", "base": base, "target": target, "markers": list(markers)}


def test_pathref_resolves_files_and_folders_by_marker():
    index = _index(["deploy/web/Dockerfile", "deploy/web/app.py", ".github/actions/setup/action.yml"])

    assert pathref.resolve(_ref("./web", "deploy", "dir", ("Dockerfile",)), index) == {
        "status": "verified", "to": "deploy/web/Dockerfile", "rule": "dir"}
    assert pathref.resolve(_ref("./.github/actions/setup", "", "any", ("action.yml",)), index)["to"] == ".github/actions/setup/action.yml"
    assert pathref.resolve(_ref("web/app.py", "deploy", "file"), index) == {
        "status": "verified", "to": "deploy/web/app.py", "rule": "relative"}


def test_pathref_folder_without_a_parsed_marker_uses_its_first_parsed_file():
    index = _index(["w/b.py", "w/a.txt", "w/Dockerfile"], analyzed=["w/b.py"])

    assert pathref.resolve(_ref("w", target="dir", markers=("Dockerfile",)), index)["to"] == "w/b.py"


def test_pathref_accepts_a_generator_of_paths():
    index = pathref.build_index((p for p in ["a/x.yml"]), ["a/x.yml"])

    assert pathref.resolve(_ref("x.yml", "a", "file"), index)["to"] == "a/x.yml"


def test_pathref_unresolved_reasons():
    index = _index(["deploy/.env.web", "a.py"], analyzed=["a.py"])

    assert pathref.resolve(_ref(".env.web", "deploy", "file"), index)["reason"] == "target_skipped"
    assert pathref.resolve(_ref("./missing", "deploy"), index)["reason"] == "not_found"
    assert pathref.resolve(_ref("../../outside"), index)["reason"] == "not_found"
    assert pathref.resolve(_ref("./${APP_DIR}"), index)["reason"] == "dynamic"


SOURCES = {
    "deploy/compose.yaml": "services:\n  web:\n    build: ./web\n    env_file: ./web/.env\n",
    "deploy/web/Dockerfile": "FROM python:3.12\n",
    "deploy/web/app.py": "print('hi')\n",
    ".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - uses: actions/checkout@v4\n      - uses: ./.github/actions/setup\n",
    ".github/actions/setup/action.yml": "runs:\n  using: composite\n  steps: []\n",
    "dags/load.yaml": "sql: sql/load.sql\n",
    "sql/load.sql": "SELECT 1;\n",
}


def _graph(sources, secret=()):
    files = []
    for path, text in sources.items():
        measured = {"bytes": len(text), "lines": count_lines(text), "sha256": hashlib.sha256(text.encode()).hexdigest()}
        if path in secret:
            files.append({"path": path, "status": "skipped", "reason": "secret"})
        elif language_for(path):
            files.append({"path": path, "status": "analyzed", "lang": language_for(path), **measured})
        else:
            files.append({"path": path, "status": "skipped", "reason": "unsupported_lang", **measured})
    inventory = {"schema_version": "0.1", "repo": {"name": "demo", "root": "/abs/demo", "commit": None, "dirty": None},
                 "files": files}
    return build_graph(inventory, sources.get)


def test_graph_adds_config_references_sql_references_and_externals():
    graph = _graph({**SOURCES, "deploy/web/.env": "X=1\n"}, secret={"deploy/web/.env"})
    refs = {(e["from"], e.get("via"), e["status"], e["to"] or e["reason"]) for e in graph["edges"] if e["kind"] == "REFERENCES"}

    assert refs == {
        # The Dockerfile is not parsed, so the build folder links to its first parsed file.
        ("file:deploy/compose.yaml", "compose:services.*.build", "verified", "file:deploy/web/app.py"),
        ("file:deploy/compose.yaml", "compose:services.*.env_file", "unresolved", "target_skipped"),
        ("file:.github/workflows/ci.yml", "actions:jobs.*.steps.uses", "verified", "file:.github/actions/setup/action.yml"),
        ("file:dags/load.yaml", None, "verified", "file:sql/load.sql"),
    }
    assert [(x["from"], x["package"]) for x in graph["externals"]] == [("file:.github/workflows/ci.yml", "actions/checkout")]


def test_report_keeps_sql_references_apart_from_config_references():
    graph = _graph(SOURCES)
    files = [n for n in graph["nodes"] if n["type"] == "file"]
    anchors = {n["id"]: n["id"] for n in files}

    config = _config_references(graph["edges"], anchors)
    sql = _file_references(graph["edges"], files, anchors)
    assert "compose:services.*.build" in config and "actions:jobs.*.steps.uses" in config
    assert "compose" not in sql and "actions" not in sql
    assert "deploy/compose.yaml:3" in config


def test_compose_file_named_after_its_purpose_is_recognised_by_content():
    refs, _ = _refs("scripts/ci/docker-compose/base.yml", "services:\n  airflow:\n    image: x\n    volumes:\n      - ../../../:/opt/airflow\n")

    assert refs == [("compose:services.*.volumes", "../../../", "scripts/ci/docker-compose", "any")]


def test_a_services_key_without_images_or_builds_is_not_compose():
    refs, _ = _refs("config/app.yml", "services:\n  billing:\n    url: ./billing\n")

    assert refs == []


def test_quoted_scalar_spanning_lines_is_skipped_without_degrading():
    text = ('example: "[%%(asctime)s]\n'
            '  {{%%(filename)s}} \\" still inside"\n'
            "note: 'it''s\n"
            "  {{ also inside }}'\n"
            "after: ./kept\n")

    assert _flat(text) == ([(("after",), "./kept")], False)
