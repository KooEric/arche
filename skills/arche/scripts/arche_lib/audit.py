"""Conservative, source-backed audit candidates; no claim of runtime reachability."""
import ast
from collections import defaultdict


def _protected(path):
    parts = path.lower().split('/')
    return (any(p in {'tests', 'test', 'generated', 'vendor', 'fixtures', 'examples'} for p in parts)
            or parts[-1].startswith('test_') or parts[-1] in {'__init__.py', '__main__.py', 'setup.py', 'conftest.py'}
            or parts[-1].endswith('_test.py'))


def build_audit(graph, read_path, internal_exports=()):
    """Identify review candidates in parsed Python files using hashed snapshot content."""
    files = sorted(n['id'][5:] for n in graph['nodes'] if n['type'] == 'file' and n.get('lang') == 'python')
    trees = {}
    for path in files:
        source = read_path(path)
        if source is None:
            continue
        try:
            trees[path] = ast.parse(source)
        except (SyntaxError, ValueError, RecursionError):
            continue
    # Any name reference anywhere suppresses a candidate, including dynamic and
    # framework conventions where the identifier is written as a string.
    references = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                references.add(node.id)
            elif isinstance(node, ast.Attribute):
                references.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                references.add(node.value)
            elif isinstance(node, ast.alias):
                references.add(node.name.split('.')[-1])
    # Explicit __all__ names are not uses by themselves. Keep a separate
    # reference set for the opt-in export rule, including all other strings.
    export_references = set()
    for tree in trees.values():
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__all__' for t in stmt.targets):
                continue
            for node in ast.walk(stmt):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    export_references.add(node.id)
                elif isinstance(node, ast.Attribute):
                    export_references.add(node.attr)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    export_references.add(node.value)
                elif isinstance(node, ast.alias):
                    export_references.add(node.name.split('.')[-1])
    findings = []
    blocks = defaultdict(list)
    connected = set()
    uncertain = set()
    for edge in graph['edges']:
        if edge.get('kind') != 'IMPORTS':
            continue
        if edge.get('to'):
            connected.update((edge['from'][5:], edge['to'][5:]))
        else:
            uncertain.add(edge['from'][5:])
    for path, tree in sorted(trees.items()):
        if _protected(path):
            continue
        declared = set()
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id == '__all__' and isinstance(stmt.value, (ast.List, ast.Tuple)):
                if all(isinstance(x, ast.Constant) and isinstance(x.value, str) for x in stmt.value.elts):
                    declared.update(x.value for x in stmt.value.elts)
        internal = any(path.startswith(prefix.rstrip('/') + '/') for prefix in internal_exports)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Public names may be library APIs. Only private definitions are
                # candidates; decorators can register functions implicitly.
                if node.name.startswith('_') and not node.name.startswith('__') and not node.decorator_list and node.name not in references:
                    findings.append({'rule': 'unused-private-symbol', 'path': path, 'line': node.lineno, 'name': node.name})
                if internal and '*' not in export_references and node.name in declared and not node.name.startswith('_') and not node.decorator_list and node.name not in export_references:
                    findings.append({'rule': 'unused-internal-export', 'path': path, 'line': node.lineno, 'name': node.name})
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.decorator_list and len(node.body) >= 4:
                    key = ast.dump(ast.Module(body=node.body, type_ignores=[]), include_attributes=False)
                    blocks[key].append((path, node.lineno, node.name))
        if path not in connected and path not in uncertain and not graph.get('skipped'):
            findings.append({'rule': 'isolated-module', 'path': path, 'line': 1})
    for locations in blocks.values():
        if len(locations) > 1:
            locations.sort()
            findings.append({'rule': 'duplicate-block', 'path': locations[0][0], 'line': locations[0][1],
                             'locations': [{'path': p, 'line': n, 'name': name} for p, n, name in locations]})
    return sorted(findings, key=lambda f: (f['rule'], f['path'], f['line']))


def finding_key(finding):
    """Stable identity ignores line movement; duplicate groups include all names."""
    if finding['rule'] == 'duplicate-block':
        return (finding['rule'], tuple(sorted((x['path'], x['name']) for x in finding['locations'])))
    return (finding['rule'], finding['path'], finding.get('name', ''))


def delta(base, head):
    before = {finding_key(f): f for f in base}
    after = {finding_key(f): f for f in head}
    return {'introduced': [after[k] for k in sorted(after.keys() - before.keys())],
            'resolved': [before[k] for k in sorted(before.keys() - after.keys())],
            'unchanged': [after[k] for k in sorted(after.keys() & before.keys())]}
