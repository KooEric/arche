from arche_lib.report import _feature_focus


def test_feature_focus_is_scoped_to_cited_http_lines_and_escapes():
    tour = {'steps': [
        {'id': 'screen', 'path': 'ui.py', 'start_line': 1, 'end_line': 5},
        {'id': 'request', 'path': 'ui.py', 'start_line': 10, 'end_line': 15},
        {'id': 'handler', 'path': 'api.py', 'start_line': 20, 'end_line': 25},
        {'id': 'unrelated', 'path': 'api.py', 'start_line': 1, 'end_line': 3}],
        'edges': [{'from': 'screen', 'to': 'request', 'kind': 'calls'}],
        'screens': [{'title': 'Home', 'items': [{'step': 'screen', 'label': '<Open>', 'behavior': 'Open view'}]}]}
    conn = {'status': 'inferred', 'request': {'file': 'ui.py', 'line': 12, 'method': 'GET', 'path': '/x'},
            'candidates': [{'file': 'api.py', 'line': 19, 'handler_line': 22}]}
    html = _feature_focus(tour, [conn], {'file:ui.py': 'u', 'file:api.py': 'a'})
    assert '&lt;Open&gt;' in html and '<Open>' not in html
    panel = html.split('data-focus-panel="0"')[1].split('data-focus-panel="1"')[0]
    assert 'screen' in panel and 'request' in panel and 'handler' in panel
    assert 'unrelated' not in panel and 'inferred HTTP' in panel and 'calls' in panel
    conn['request']['line'] = 8
    html = _feature_focus(tour, [conn], {})
    assert 'inferred HTTP</strong>' not in html


def test_focus_empty_without_tour():
    assert _feature_focus(None, [], {}) == ''
