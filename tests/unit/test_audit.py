from arche_lib.audit import build_audit, delta


def audit(sources, edges=(), skipped=()):
    graph = {'nodes': [{'type': 'file', 'id': 'file:' + p, 'lang': 'python'} for p in sources],
             'edges': list(edges), 'skipped': list(skipped)}
    return build_audit(graph, sources.get)


def test_candidates_and_false_positive_controls():
    sources = {
        'app.py': 'def _unused():\n    pass\n\ndef public_api():\n    pass\n\n@register\ndef _hook():\n    pass\n',
        'client.py': 'value = "_used"\n',
        'lib.py': 'def _used():\n    pass\n',
        'tests/test_app.py': 'def _test_helper():\n    pass\n',
        'generated/code.py': 'def _generated():\n    pass\n',
        '__init__.py': 'def _entry():\n    pass\n',
    }
    findings = audit(sources)
    names = {f.get('name') for f in findings if f['rule'] == 'unused-private-symbol'}
    assert names == {'_unused'}
    assert not any(f['path'].startswith(('tests/', 'generated/')) for f in findings)
    assert not any(f['path'] == '__init__.py' for f in findings)
    assert all(f['line'] >= 1 for f in findings)
    assert not any(f['rule'] == 'isolated-module' for f in audit(sources, skipped=[{'path': 'x'}]))


def test_duplicates_and_delta_ignore_line_movement():
    body = '    a = 1\n    b = 2\n    c = a + b\n    return c\n'
    before = audit({'a.py': 'def one():\n' + body, 'b.py': 'def two():\n' + body})
    after = audit({'a.py': '\n\ndef one():\n' + body, 'b.py': 'def two():\n' + body})
    dup = [f for f in before if f['rule'] == 'duplicate-block']
    assert len(dup) == 1 and len(dup[0]['locations']) == 2
    changes = delta(before, after)
    assert not changes['introduced'] and not changes['resolved']
    assert len(changes['unchanged']) == len(before)
    assert delta(before, [])['resolved'] == before


def test_real_clean_base_head_finding_delta(make_repo, run_script, tmp_path, git_cmd):
    import json
    repo = make_repo({'app.py': 'def _old():\n    pass\n'})
    base = git_cmd(repo, 'rev-parse', 'HEAD').strip()
    base_out = tmp_path / 'base'
    for script, args in [('scan.py', (repo, '--out', base_out)), ('imports.py', ('--out', base_out)), ('render.py', ('--out', base_out))]:
        result = run_script(script, *args)
        assert result.returncode == 0, result.stderr
    (repo / 'app.py').write_text('def _new():\n    pass\n')
    git_cmd(repo, 'add', 'app.py')
    git_cmd(repo, 'commit', '-m', 'replace candidate')
    head = git_cmd(repo, 'rev-parse', 'HEAD').strip()
    head_out = tmp_path / 'head'
    for script, args in [('scan.py', (repo, '--out', head_out)), ('imports.py', ('--out', head_out)),
                         ('changes.py', ('--out', head_out, '--base', base, '--head', head)),
                         ('render.py', ('--out', head_out, '--base-out', base_out))]:
        result = run_script(script, *args)
        assert result.returncode == 0, result.stderr
    result = json.loads((head_out / 'audit.json').read_text())['delta']
    assert [f['name'] for f in result['introduced']] == ['_new']
    assert [f['name'] for f in result['resolved']] == ['_old']
    assert any(f['rule'] == 'isolated-module' for f in result['unchanged'])
    html = (head_out / 'report.html').read_text()
    assert all(x in html for x in ('Finding changes', 'Introduced', 'Resolved', '_new', '_old'))


def test_internal_exports_are_explicitly_opted_in_and_references_suppress():
    sources = {'internal/api.py': '__all__ = ["unused", "used"]\ndef unused():\n    pass\ndef used():\n    pass\n',
               'consumer.py': 'from internal.api import used\n'}
    graph = {'nodes': [{'type': 'file', 'id': 'file:' + p, 'lang': 'python'} for p in sources], 'edges': [], 'skipped': []}
    assert not any(f['rule'] == 'unused-internal-export' for f in build_audit(graph, sources.get))
    findings = build_audit(graph, sources.get, ['internal'])
    assert [f['name'] for f in findings if f['rule'] == 'unused-internal-export'] == ['unused']
    assert not any(f['rule'] == 'unused-internal-export' for f in build_audit(graph, sources.get, ['other']))
