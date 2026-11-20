import json

from arche_lib.diagrams import feature_map, system_map


def test_map_uses_curated_roles_and_preserves_import_and_http_evidence():
    modules = [{"name": name, "parsed": 1} for name in ("app", "worker", "unmapped")]
    tour = {"overview": {"summary": "<summary>", "modules": [
        {"module": name, "title": name.upper(), "description": "<role>", "step": name}
        for name in ("app", "worker")]},
        "steps": [{"id": name, "path": f"{name}/main.py", "start_line": 1, "end_line": 2}
                  for name in ("app", "worker")], "edges": []}
    connection = {"status": "inferred", "request": {"file": "app/main.py", "method": "GET", "path": "/items", "line": 2},
                  "candidates": [{"file": "worker/main.py", "handler_line": 2, "line": 1}]}
    graph = {"edges": [{"from": "file:app/main.py", "to": "file:worker/main.py", "status": "verified"}],
             "http_connections": [connection]}
    anchors = {"file:app/main.py": "file-0", "file:worker/main.py": "file-1"}
    html = system_map(modules, graph, tour, anchors)
    assert "&lt;summary&gt;" in html and "&lt;role&gt;" in html
    assert "Showing 2 of 3" in html and 'href="#file-0"' in html and "inferred HTTP" in html
    flow = feature_map(tour, graph["http_connections"], anchors)
    assert "http_candidate" in flow and "main.py:1–2" in flow and 'href="#file-1"' in flow
    # A request on a different line must not create a connection in this view.
    changed = json.loads(json.dumps(connection))
    changed["request"]["line"] = 9
    assert "http_candidate" not in feature_map(tour, [changed], anchors)
