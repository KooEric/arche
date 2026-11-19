import json
import subprocess
import sys
from pathlib import Path

import pytest

from export import export_document, render_export
from arche_lib.errors import IntegrityError

SCRIPT = Path(__file__).resolve().parents[2] / "skills/arche/scripts/export.py"


def fixture():
    digest = "a" * 64
    inventory = {"repo": {"name": 'repo<evil>', "commit": "abc123", "dirty": True},
                 "files": [{"path": 'src/a"].py', "sha256": digest}]}
    tour = {"version": 1, "steps": [{"id": 'x"]\n<script>', "path": 'src/a"].py',
             "start_line": 2, "end_line": 3, "sha256": digest}],
            "edges": [{"from": 'x"]\n<script>', "to": 'x"]\n<script>', "kind": "calls"}]}
    return inventory, tour


def test_exports_preserve_evidence_and_escape():
    inventory, tour = fixture()
    document = export_document(inventory, tour)
    assert json.loads(render_export(document, "json")) == {"version": 1, "repo": inventory["repo"], "tour": tour}
    md = render_export(document, "markdown")
    diagram = render_export(document, "mermaid")
    for output in (md, diagram):
        assert "abc123" in output and "a" * 64 in output
        assert "<script>" not in output and "&#10;" in output
        assert "&#34;" in output and "&#93;" in output
    assert 'n0 -->|"calls"| n0' in diagram


@pytest.mark.parametrize("version", [None, 2, 1.0, True, "1"])
def test_rejects_unsupported_versions(version):
    inventory, tour = fixture()
    tour["version"] = version
    with pytest.raises(IntegrityError, match="version"):
        export_document(inventory, tour)


def test_rejects_mismatched_snapshot_and_edges():
    inventory, tour = fixture()
    inventory["files"][0]["sha256"] = "b" * 64
    with pytest.raises(IntegrityError, match="inventory"):
        export_document(inventory, tour)
    inventory, tour = fixture()
    tour["edges"][0]["to"] = "missing"
    with pytest.raises(IntegrityError, match="edge"):
        export_document(inventory, tour)


def test_cli_exports_without_source_files(tmp_path):
    inventory, tour = fixture()
    run = tmp_path / "run"
    run.mkdir()
    (run / "inventory.json").write_text(json.dumps(inventory))
    tour_path = tmp_path / "tour.json"
    tour_path.write_text(json.dumps(tour))
    output = tmp_path / "export.json"
    result = subprocess.run([sys.executable, str(SCRIPT), "--out", str(run), "--tour", str(tour_path),
                             "--format", "json", "--output", str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text())["tour"] == tour
    assert json.loads(output.read_text())["repo"]["commit"] == "abc123"
