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
        ("python:yaml_path", "orders.yaml", "file", False),
        ("python:yaml_glob", "configs/*.yml", "glob", False),
        ("python:yaml_path", "{}/{}/conf.yaml", "file", True),
    ]


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
        ("dags/sda/orders/dag.py", "python:yaml_path", "inferred", "python_yaml", "file:dags/sda/orders/orders.yaml"),
        ("dags/sda/users/dag.py", "python:same_folder_yaml", "inferred", "same_folder", "file:dags/sda/users/users.yaml"),
        ("dags/cds/x/build.py", "python:yaml_path", "unresolved", "dynamic", None),
        ("dags/factory.py", "python:yaml_glob", "inferred", "glob", "file:dags/configs/a.yaml"),
    }  # dags/cli.py loads YAML but its folder holds none: no edge


def test_shape_never_needs_the_value_and_classifies_it():
    exists = {"sql/a.sql"}.__contains__
    assert [shape(v, exists) for v in ["sql/a.sql", "s3://b/k", "{{ ds }}", "42", "true", "dw.orders", "owner_a", "a/b", "hello world"]] == [
        "existing_file", "url", "template", "number", "bool", "dotted_name", "word", "path", "text"]


def test_diagnosis_groups_by_top_keys_and_reports_signals_without_values():
    result = diagnose(SOURCES, SOURCES.get, linked={"dags/sda/orders/orders.yaml"})
    groups = {tuple(g["top_keys"]): g for g in result["groups"]}

    sda = groups[("dag_id", "owner")]
    assert (sda["files"], sda["linked"], sda["signals"], sda["uncovered"]) == (
        2, 1, {"path_value": 0, "python_literal": 1, "same_folder_py": 2}, 0)
    cds = groups[("owner",)]
    assert (cds["signals"]["python_literal"], cds["uncovered"]) == (0, 0)  # an f-string is not a written name
    text = render_text(result)
    assert "sda.orders" not in text and "owner: word 3" not in text and "dag_id: dotted_name 2" in text


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
