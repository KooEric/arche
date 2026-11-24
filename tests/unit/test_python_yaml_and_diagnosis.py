import json
import subprocess
import sys
from pathlib import Path

from arche_lib.langs.python_yamlrefs import yaml_refs
from arche_lib.yamldiag import diagnose, render_text, shape
from test_yaml_config import _graph

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "arche" / "scripts"


def _refs(text, path="dags/x/dag.py"):
    return [(r["via"], r["spec"], r["target"], r.get("dynamic", False)) for r in yaml_refs(path, text)]


def test_literal_yaml_names_patterns_and_fstrings():
    text = ("from pathlib import Path\nimport glob, yaml\n"
            "conf = yaml.safe_load(open(Path(__file__).parent / 'orders.yaml'))\n"
            "for f in glob.glob('configs/*.yml'): pass\n"
            "dynamic = f'{BASE}/{team}/conf.yaml'\n"
            "not_a_path = 'see the .yaml docs'\n")

    assert _refs(text) == [
        ("python:yaml_name", "orders.yaml", "name", False),
        ("python:yaml_glob", "configs/*.yml", "glob", False),
        ("python:yaml_name", "conf.yaml", "name", False),
    ]


def test_names_attached_to_run_time_folders_and_fully_dynamic_fstrings():
    text = ("import os\n"
            "a = os.path.join(dag_dir, 'dag_config.yaml')\n"
            "b = base + '/b.yaml'\n"
            "c = f'{base}/{name}.yaml'\n"
            "d = load('standalone.yaml')\n"
            "e = os.path.join('fixed', 'dir.yaml')\n")

    assert _refs(text) == [
        ("python:yaml_name", "dag_config.yaml", "name", False),
        ("python:yaml_name", "b.yaml", "name", False),
        ("python:yaml_path", "{}/{}.yaml", "file", True),
        ("python:yaml_name", "standalone.yaml", "name", False),
        ("python:yaml_name", "dir.yaml", "name", False),
    ]


def test_a_written_name_with_a_folder_keeps_the_path_lookup():
    assert _refs("show_only = ['templates/api/deploy.yaml']\n") == [
        ("python:yaml_path", "templates/api/deploy.yaml", "file", False)]


def test_a_yaml_loader_without_a_file_name_falls_back_to_its_own_folder():
    assert _refs("from common import load_yaml_file\nconf = load_yaml_file(CONF)\n") == [
        ("python:same_folder_yaml", "*.{yaml,yml}", "glob", False)]
    assert _refs("import yaml\nyaml.safe_load(stream)\n") == [("python:same_folder_yaml", "*.{yaml,yml}", "glob", False)]


def test_files_without_yaml_are_not_walked():
    assert yaml_refs("a.py", "print('hi')\n") == []


SOURCES = {
    "dags/sda/orders/dag.py": "import yaml\nconf = yaml.safe_load(open(DAG_DIR / 'orders.yaml'))\n",
    "dags/sda/orders/orders.yaml": "owner: a\ndag_id: sda.orders\n",
    "dags/sda/users/dag.py": "from common import load_yaml_file\nconf = load_yaml_file(CONF)\n",
    "dags/sda/users/users.yaml": "owner: b\ndag_id: sda.users\n",
    "dags/cds/x/build.py": "from common import load_yaml_file\nconf = load_yaml_file(f'{BASE}/{team}/conf.yaml')\n",
    "dags/cds/x/conf.yaml": "owner: c\n",
    "dags/factory.py": "import glob, yaml\nfor f in glob.glob(os.path.join(DIR, 'configs/*.yaml')):\n    yaml.safe_load(open(f))\n",
    "dags/configs/a.yaml": "x: 1\n",
    "dags/configs/b.yaml": "x: 2\n",
    "dags/cli.py": "import yaml\nyaml.safe_load(open(ARGS.config))\n",
}


def test_graph_links_python_to_the_yaml_it_reads():
    edges = {(e["from"][5:], e["via"], e["status"], e.get("rule") or e.get("reason"), e["to"])
             for e in _graph(SOURCES)["edges"] if str(e.get("via", "")).startswith("python:")}

    assert edges == {
        ("dags/sda/orders/dag.py", "python:yaml_name", "inferred", "same_name", "file:dags/sda/orders/orders.yaml"),
        ("dags/sda/users/dag.py", "python:same_folder_yaml", "inferred", "same_folder", "file:dags/sda/users/users.yaml"),
        ("dags/cds/x/build.py", "python:yaml_name", "inferred", "same_name", "file:dags/cds/x/conf.yaml"),
        ("dags/factory.py", "python:yaml_glob", "inferred", "glob", "file:dags/configs/a.yaml"),
    }  # dags/cli.py loads YAML but its folder holds none: no edge


def test_shape_never_needs_the_value_and_classifies_it():
    exists = {"sql/a.sql"}.__contains__
    assert [shape(v, exists) for v in ["sql/a.sql", "s3://b/k", "{{ ds }}", "42", "true", "dw.orders", "owner_a", "a/b", "hello world"]] == [
        "existing_file", "url", "template", "number", "bool", "dotted_name", "word", "path", "text"]


def test_diagnosis_reports_what_arche_links_by_rule_without_values():
    python = [p for p in SOURCES]
    result = diagnose(python, list(SOURCES), SOURCES.get, linked={"dags/sda/orders/orders.yaml"})
    groups = {tuple(g["top_keys"]): g for g in result["groups"]}

    sda = groups[("dag_id", "owner")]
    assert (sda["files"], sda["linked"], sda["linked_by_python"], sda["no_evidence"]) == (2, 1, 2, 0)
    assert sda["python_rules"] == {"python:same_folder_yaml": 1, "python:yaml_name": 1}
    text = render_text(result)
    assert "sda.orders" not in text and "dag_id: dotted_name 2" in text


def test_diagnosis_names_the_folders_of_python_that_writes_an_unresolvable_name():
    sources = {"common/cds.py": "CONF = 'conf/tasks.yaml'\n", "dags/c/tasks.yaml": "owner: y\n"}
    group = diagnose(list(sources), list(sources), sources.get)["groups"][0]

    assert (group["linked_by_python"], group["name_unresolved"], group["name_written_in"], group["no_evidence"]) == (
        0, 1, ["common"], 0)


def test_diagnosis_counts_files_with_no_evidence():
    sources = {"dags/c/tasks.yaml": "owner: y\n"}
    group = diagnose(list(sources), list(sources), sources.get)["groups"][0]

    assert (group["linked_by_python"], group["name_unresolved"], group["no_evidence"]) == (0, 0, 1)


def test_diagnose_cli_writes_reports_outside_the_repository(tmp_path):
    repo = tmp_path / "repo"
    for path, text in SOURCES.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    done = subprocess.run([sys.executable, "-B", str(SCRIPTS / "diagnose_yaml.py"), str(repo), "--output", str(tmp_path / "out")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["yaml_files"] == 5
    assert (tmp_path / "out" / "yaml-diagnosis.txt").exists()
    inside = subprocess.run([sys.executable, "-B", str(SCRIPTS / "diagnose_yaml.py"), str(repo), "--output", str(repo / "out")],
                            capture_output=True, text=True)
    assert inside.returncode != 0


def test_a_shared_loader_links_to_every_file_of_the_name_it_builds():
    sources = {
        "common/loader.py": "import yaml\ndef load(dag_dir):\n    return yaml.safe_load(open(dag_dir / 'dag_config.yaml'))\n",
        "dags/a/dag.py": "from common.loader import load\nconf = load(DAG_DIR)\n",
        "dags/a/dag_config.yaml": "dag_id: a\n",
        "dags/b/dag_config.yaml": "dag_id: b\n",
        "dags/b/other.yaml": "x: 1\n",
    }
    edges = sorted((e["from"], e["to"], e["rule"], e.get("glob"), e.get("matched"), e["evidence"]["path"])
                   for e in _graph(sources)["edges"] if e.get("via") == "python:yaml_name")

    assert edges == [
        # The loader: one edge with the pattern and how many files it covers.
        ("file:common/loader.py", "file:dags/a/dag_config.yaml", "same_name", "**/dag_config.yaml", 2, "common/loader.py"),
        # Each file to the code in its own folder, citing the loader; dags/b has no Python.
        ("file:dags/a/dag.py", "file:dags/a/dag_config.yaml", "same_folder_name", None, None, "common/loader.py"),
    ]


def test_a_change_to_any_file_the_loader_builds_reaches_the_loader():
    from arche_lib.changes import _dependents, _reverse_imports
    sources = {
        "common/loader.py": "def load(d):\n    return open(d / 'dag_config.yaml')\n",
        "dags/a/dag_config.yaml": "dag_id: a\n",
        "dags/b/dag_config.yaml": "dag_id: b\n",
    }

    assert _dependents(_reverse_imports(_graph(sources)), "dags/b/dag_config.yaml") == ["common/loader.py"]


def test_diagnosis_matches_the_edges_drawn_for_a_shared_loader():
    sources = {
        "common/loader.py": "def load(d):\n    return open(d / 'dag_config.yaml')\n",
        "dags/a/dag.py": "from common.loader import load\n",
        "dags/a/dag_config.yaml": "dag_id: a\n",
        "dags/b/dag.py": "from common.loader import load\n",
        "dags/b/dag_config.yaml": "dag_id: b\n",
        "dags/c/dag_config.yaml": "dag_id: c\n",
    }
    group = diagnose(list(sources), list(sources), sources.get)["groups"][0]
    drawn = {e["to"][5:] for e in _graph(sources)["edges"] if e.get("via") == "python:yaml_name"}

    assert drawn == {"dags/a/dag_config.yaml", "dags/b/dag_config.yaml"}
    assert (group["files"], group["linked_by_python"], group["pattern_only"], group["no_evidence"]) == (3, 2, 1, 0)
