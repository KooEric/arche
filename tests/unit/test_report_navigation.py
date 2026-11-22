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


def linked():
    graph = sample()
    graph['nodes'].append({'type': 'file', 'id': 'file:tests/test_a.py', 'lang': 'python', 'lines': 9})
    graph['nodes'].append({'type': 'file', 'id': 'file:x<y.py', 'lang': 'python', 'lines': 3})
    evidence = {'path': 'main.py', 'line': 1}
    graph['edges'] = [
        {'from': 'file:main.py', 'to': 'file:pkg/a.py', 'status': 'verified', 'evidence': evidence},
        {'from': 'file:main.py', 'to': 'file:pkg/a.py', 'status': 'inferred', 'evidence': evidence},
        {'from': 'file:tests/test_a.py', 'to': 'file:pkg/a.py', 'status': 'verified', 'evidence': evidence},
    ]
    return graph


def test_key_files_rank_entries_and_hubs_without_tests():
    html = render(TEMPLATE, linked(), [], 'match')
    entries = html.split('Entry point candidates')[1].split('Most depended on')[0]
    assert 'main.py' in entries and 'test_a.py' not in entries
    hubs = html.split('Most depended on')[1].split('Largest files')[0]
    assert 'pkg/a.py' in hubs and '2 importers' in hubs
    assert '2 in · 0 out' in html


def test_map_data_merges_edges_and_cannot_close_its_script_block():
    html = render(TEMPLATE, linked(), [], 'match')
    block = html.split('<script type="application/json" id="graph-data">')[1].split('</script>')[0]
    assert '<' not in block
    data = __import__('json').loads(block)
    assert data['files'][3] == ['x<y.py', 3]
    assert data['edges'] == [[1, 0, 2, 1], [2, 0, 1, 0]]


def test_korean_interface_and_tour_narrative():
    tour = {'title': '요약 <제목>', 'summary': '첫 문단\n\n둘째 문단', 'edges': [], 'steps': [
        {'id': 'start', 'title': '시작', 'note': '설명 <b>', 'path': 'main.py', 'start_line': 1, 'end_line': 1, 'sha256': 'a' * 64}]}
    html = render(TEMPLATE, sample(), [{'commit': 'c' * 40, 'date': '2026-01-02T03:04:05+00:00', 'subject': 'Fix <it>'}], 'match', tour=tour, lang='ko')
    assert '<html lang="ko">' in html and '의존성 맵' in html and '핵심 파일' in html
    assert '요약 &lt;제목&gt;' in html and '<p>첫 문단</p><p>둘째 문단</p>' in html
    assert '설명 &lt;b&gt;' in html and 'Fix &lt;it&gt;' in html and 'c' * 40 in html
    assert '>Dependency map<' not in html
    assert '>Dependency map<' in render(TEMPLATE, linked(), [], 'match')


def test_large_reports_send_file_details_as_data():
    graph = linked()
    graph['nodes'].append({'type': 'symbol', 'id': 'sym:pkg/a.py#secret<name', 'kind': 'function', 'line': 2})
    eager = render(TEMPLATE, graph, [], 'match')
    lazy = render(TEMPLATE, graph, [], 'match', lazy_file_limit=2)
    assert '<script type="application/json" id="file-details">null</script>' in eager
    assert '<code>secret&lt;name</code>' in eager and '<div class="detail">' in eager
    # Every file keeps its row and anchor; the details move into the data block.
    assert lazy.count('<details class="file"') == eager.count('<details class="file"') == 4
    assert '<div class="detail">' not in lazy.split('<script type="application/json"')[0]
    block = lazy.split('id="file-details">')[1].split('</script>')[0]
    data = __import__('json').loads(block)
    assert '<' not in block and data[0]['s'] == [['secret<name', 'function', 2]]
    assert data[1]['o'][0][:4] == [0, '', 'verified', 1]
