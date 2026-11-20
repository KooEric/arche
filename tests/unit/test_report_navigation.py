"""Fixed sample for the report's module and evidence navigation contract."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills/arche/scripts'))
from arche_lib.report import render

TEMPLATE = (Path(__file__).resolve().parents[2] / 'skills/arche/assets/report-template.html').read_text()


def sample():
    return {
        'repo': {'name': 'sample', 'commit': None, 'dirty': None},
        'nodes': [
            {'type': 'file', 'id': 'file:pkg/a.py', 'lang': 'python', 'lines': 2},
            {'type': 'file', 'id': 'file:main.py', 'lang': 'python', 'lines': 1},
        ],
        'edges': [], 'externals': [],
        'skipped': [{'path': 'assets/logo.png', 'reason': 'unsupported_lang'}],
    }


def test_modules_and_tour_link_to_existing_file_anchors():
    tour = {'steps': [
        {'id': 'start', 'path': 'main.py', 'start_line': 1, 'end_line': 1, 'sha256': 'a' * 64},
        {'id': 'next', 'path': 'pkg/a.py', 'start_line': 1, 'end_line': 2, 'sha256': 'b' * 64},
    ], 'edges': [{'from': 'start', 'to': 'next', 'kind': 'artifact_transfer'}]}
    html = render(TEMPLATE, sample(), [], 'match', modules=[
        {'name': '.', 'parsed': 1, 'excluded': 0},
        {'name': 'pkg', 'parsed': 1, 'excluded': 0},
        {'name': 'assets', 'parsed': 0, 'excluded': 1},
    ], tour=tour)
    assert 'data-select-module="assets"' in html
    assert '0 parsed · 1 excluded' in html
    assert 'data-file="pkg/a.py" data-module="pkg"' in html
    assert 'href="#file-0"><code>pkg/a.py</code></a>:1–2' in html
    assert '<code>start</code> → <code>next</code> · artifact_transfer' in html
    assert 'b' * 64 in html
    assert 'unsupported_lang' in html


def test_empty_and_escaping():
    graph = sample()
    graph['nodes'] = []
    html = render(TEMPLATE, graph, [], 'match', modules=[{'name': '<img>', 'parsed': 0, 'excluded': 1}], tour={'steps': [], 'edges': []})
    assert 'data-select-module="&lt;img&gt;"' in html
    assert 'No tour available for this snapshot.' in html
    assert 'No files parsed by the installed adapters.' in html
    assert 'No matching files.' in html


def test_http_rows_show_an_escaped_host_and_no_missing_route_note_for_outside_hosts():
    from arche_lib.report import _http_section
    request = {"file": "a.ts", "line": 1, "method": "GET", "path": "/x", "dynamic": False,
               "host": "<cdn>.example.com"}
    html = _http_section([{"request": request, "candidates": [], "reason": "external_host",
                           "status": "unresolved"}], {})
    assert "&lt;cdn&gt;.example.com" in html and "<cdn>" not in html
    assert "No matching literal route identified." not in html
