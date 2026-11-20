import hashlib
import json

import pytest

from arche_lib.errors import IntegrityError
from arche_lib.jsonio import check_graph, inventory_digest, read_snapshot
from arche_lib.errors import UsageError
from focus import select, mermaid


def test_focus_follows_unique_http_only_at_evidence_lines():
    def step(id_, path, line):
        return dict(id=id_, path=path, start_line=line, end_line=line)
    tour = {"steps": [step("button", "view", 5), step("request", "api", 9),
                      step("handler", "worker", 20), step("write", "db", 10), step("other", "worker", 21)],
            "edges": [{"from": "button", "to": "request", "kind": "calls"},
                      {"from": "handler", "to": "write", "kind": "writes"}],
            "screens": [{"title": "Home", "items": [{"step": "button", "label": "Save", "behavior": "save draft"}]}]}
    conn = {"status": "inferred", "request": {"file": "api", "line": 9, "method": "POST", "path": "/draft"},
            "candidates": [{"file": "worker", "line": 11, "handler_line": 20}]}
    result = select(tour, "Save", 3, [conn])
    assert [s["id"] for s in result["steps"]] == ["button", "request", "handler", "write"]
    assert len(result["http_candidates"]) == 1 and "inferred HTTP" in mermaid(result)
    assert len(select(tour, "Save", 3, [dict(conn, status="unresolved")])["steps"]) == 2
    with pytest.raises(UsageError):
        select(tour, "unused", 1)


def test_schema_compatibility_and_dirty_snapshot_mismatch(tmp_path):
    path = tmp_path / "inventory.json"
    repo = {"name": "synthetic", "commit": "a" * 40, "dirty": True}
    old = {"schema_version": "0.1", "repo": repo, "files": [{"path": "a.py", "sha256": "f" * 64}]}
    path.write_text(json.dumps(old))
    assert read_snapshot(path) == old
    legacy = {"repo": repo, "nodes": [{"id": "file:a.py", "type": "file", "sha256": "f" * 64}]}
    check_graph(old, legacy)
    graph = dict(legacy, schema_version="1.0", inventory_sha256=inventory_digest(old))
    check_graph(old, graph)
    old["files"][0]["sha256"] = hashlib.sha256(b"modified").hexdigest()
    for other in [legacy, graph]:
        with pytest.raises(IntegrityError):
            check_graph(old, other)
    path.write_text('{"schema_version":"2.0"}')
    with pytest.raises(IntegrityError, match="unsupported"):
        read_snapshot(path)


def test_two_snapshot_deleted_dependency(make_repo, run_script, git_cmd, tmp_path):
    repo = make_repo({"old.py": "x = 1\n", "user.py": "import old\n"})
    def scan(out):
        for cmd, args in [("scan.py", [repo]), ("imports.py", [])]:
            assert run_script(cmd, *args, "--out", out).returncode == 0
    base, head = tmp_path / "base", tmp_path / "head"
    scan(base)
    (repo / "old.py").unlink()
    (repo / "user.py").write_text("pass\n")
    git_cmd(repo, "add", "-A")
    git_cmd(repo, "commit", "-qm", "change")
    scan(head)
    result = run_script("changes.py", "--out", head, "--base-out", base, "--base", "HEAD~1", "--head", "HEAD")
    assert result.returncode == 0, result.stderr
    change = next(x for x in json.loads((head/"changes.json").read_text())["files"] if x["path"] == "old.py")
    assert change["comparison"]["base"] == {"state": "analyzed", "paths": ["user.py"]}
    assert change["comparison"]["head"]["state"] == "absent"
