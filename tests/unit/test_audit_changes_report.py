from arche_lib.report import _audit, _changes


def edge(source, target, line, **flags):
    return {"from": "file:" + source, "to": "file:" + target, "status": "verified",
            "evidence": {"path": source, "line": line}, **flags}


def test_cycle_and_duplicate_evidence_and_normal_exceptions():
    anchors = {"file:a.py": "a", "file:b.py": "b"}
    edges = [edge("a.py", "b.py", 1), edge("a.py", "b.py", 2), edge("b.py", "a.py", 3)]
    html = _audit(edges, anchors)
    assert "One observed static import cycle" in html
    assert all(location in html for location in ("a.py:1", "a.py:2", "b.py:3"))
    assert "may be redundant" in html
    normal = _audit([edges[0], edges[0], edge("b.py", "a.py", 3, conditional=True),
                     edge("a.py", "b.py", 4, type_only=True)], anchors)
    assert "No cycle found" in normal
    assert "No repeated verified" in normal


def test_changes_show_evidence_candidates_and_empty_states():
    assert "No change comparison" in _changes(None, {})
    assert "No changed files" in _changes({"files": [], "impact_available": True}, {})
    html = _changes({"impact_available": False, "files": [{"status": "renamed", "path": "new.py", "old_path": "old.py", "impact": []}]}, {})
    assert 'data-change-status="renamed"' in html
    assert "old.py" in html and "new.py" in html
    assert "impact unavailable" in html and "Impact was not computed" in html
    assert "not proven runtime effects" in html
