from arche_lib.langs import language_for
from arche_lib.langs.go import build_context, extract, resolve
from test_graph_multilang import _graph


def test_import_forms_aliases_and_comments():
    text = '''package main
// import "example.com/fake"
import (
  app "example.com/app/internal/app"
  _ "fmt"
  . `example.com/app/dot`
)
import "os"
var s = `import "example.com/no"`
'''
    imports = extract('main.go', text)['imports']
    assert [(i['spec'], i['attrs'].get('alias')) for i in imports] == [
        ('example.com/app/internal/app', 'app'), ('fmt', '_'),
        ('example.com/app/dot', '.'), ('os', None)]
    assert language_for('main.go') == 'go'


def test_graph_keeps_platform_candidates_and_conditions():
    graph = _graph({
        'go.mod': 'module example.com/app\n',
        'cmd/main.go': 'package main\nimport app "example.com/app/internal/app"\n',
        'internal/app/app.go': 'package app\nfunc Run() {}\n',
        'internal/app/exec_linux_amd64.go': '//go:build cgo && linux\n// +build cgo,linux\n\npackage app\n',
        'internal/app/exec_windows.go': 'package app\n',
        'internal/app/exec_test.go': 'package app\n',
    })
    edges = graph['edges']
    assert len(edges) == 3
    assert all(e['status'] == 'inferred' and e['alias'] == 'app' for e in edges)
    linux = next(e for e in edges if e['to'].endswith('linux_amd64.go'))
    assert linux['target_build_constraints'] == {'os': 'linux', 'arch': 'amd64',
                                                   'go_build': ['cgo && linux'], 'plus_build': ['cgo,linux']}
    windows = next(n for n in graph['nodes'] if n['id'].endswith('exec_windows.go'))
    assert windows['build_constraints'] == {'os': 'windows'}


def test_module_boundaries_and_missing():
    files = {'go.mod': 'module example.com/a\n', 'sub/go.mod': 'module example.com/a/sub\n',
             'main.go': '', 'sub/pkg/x.go': 'package pkg\n'}
    ctx = build_context(['main.go', 'sub/pkg/x.go'], files, files.get)
    assert resolve({'spec': 'example.com/a/sub/pkg'}, 'main.go', ctx)[0]['status'] == 'external'
    assert resolve({'spec': 'example.com/a/sub/pkg'}, 'sub/pkg/x.go', ctx)[0]['to'] == 'sub/pkg/x.go'
    assert resolve({'spec': 'example.com/ab'}, 'main.go', ctx)[0]['status'] == 'external'
    assert resolve({'spec': 'example.com/a/missing'}, 'main.go', ctx)[0]['reason'] == 'module_not_found'
