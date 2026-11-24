"""Conservative, source-backed audit candidates; no claim of runtime reachability."""
import ast
import hashlib
from collections import defaultdict


def _protected(path):
    parts = path.lower().split('/')
    return (any(p in {'tests', 'test', 'generated', 'vendor', 'fixtures', 'examples'} for p in parts)
            or parts[-1].startswith('test_') or parts[-1] in {'__init__.py', '__main__.py', 'setup.py', 'conftest.py'}
            or parts[-1].endswith('_test.py'))


def build_audit(graph, read_path, internal_exports=()):
    """Identify review candidates in parsed Python files using hashed snapshot content."""
    files = sorted(n['id'][5:] for n in graph['nodes'] if n['type'] == 'file' and n.get('lang') == 'python')
    # Each syntax tree is reduced to a small summary and dropped, so memory grows
    # with the summaries, not with every tree in the repository.
    summaries = {}
    references = set()
    export_references = set()
    for path in files:
        source = read_path(path)
        if source is None:
            continue
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError, RecursionError):
            continue
        _collect_references(tree, references, export_references)
        if not _protected(path):
            summaries[path] = _summarize(tree)
        del tree
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
    for path, (declared, definitions) in sorted(summaries.items()):
        internal = any(path.startswith(prefix.rstrip('/') + '/') for prefix in internal_exports)
        for name, line, decorated, block in definitions:
            # Public names may be library APIs. Only private definitions are
            # candidates; decorators can register functions implicitly.
            if name.startswith('_') and not name.startswith('__') and not decorated and name not in references:
                findings.append({'rule': 'unused-private-symbol', 'path': path, 'line': line, 'name': name})
            if internal and '*' not in export_references and name in declared and not name.startswith('_') and not decorated and name not in export_references:
                findings.append({'rule': 'unused-internal-export', 'path': path, 'line': line, 'name': name})
            if block is not None:
                blocks[block].append((path, line, name))
        if path not in connected and path not in uncertain and not graph.get('skipped'):
            findings.append({'rule': 'isolated-module', 'path': path, 'line': 1})
    for locations in blocks.values():
        if len(locations) > 1:
            locations.sort()
            findings.append({'rule': 'duplicate-block', 'path': locations[0][0], 'line': locations[0][1],
                             'locations': [{'path': p, 'line': n, 'name': name} for p, n, name in locations]})
    return sorted(findings, key=lambda f: (f['rule'], f['path'], f['line']))


def _collect_references(tree, references, export_references):
    """Any name reference anywhere suppresses a candidate, including dynamic and
    framework conventions where the identifier is written as a string.
    Explicit __all__ names are not uses by themselves, so the opt-in export
    rule keeps a second set that leaves those assignments out."""
    for stmt in tree.body:
        names = set()
        stack = [stmt]
        while stack:
            node = stack.pop()
            kind = node.__class__
            if kind is ast.Name:
                if node.ctx.__class__ is ast.Load:
                    names.add(node.id)
                continue
            if kind is ast.Constant:
                if node.value.__class__ is str:
                    names.add(node.value)
                continue
            if kind is ast.Attribute:
                names.add(node.attr)
            elif kind is ast.alias:
                names.add(node.name.split('.')[-1])
                continue
            for field in node._fields:
                value = getattr(node, field, None)
                if value.__class__ is list:
                    stack.extend(item for item in value if isinstance(item, ast.AST))
                elif isinstance(value, ast.AST):
                    stack.append(value)
        references |= names
        if not (isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__all__' for t in stmt.targets)):
            export_references |= names


def _summarize(tree):
    """(__all__ names, [(name, line, decorated, body digest or None)]) for top-level definitions."""
    declared = set()
    definitions = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id == '__all__' and isinstance(stmt.value, (ast.List, ast.Tuple)):
            if all(isinstance(x, ast.Constant) and isinstance(x.value, str) for x in stmt.value.elts):
                declared.update(x.value for x in stmt.value.elts)
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            block = None
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and not stmt.decorator_list and len(stmt.body) >= 4:
                dump = ast.dump(ast.Module(body=stmt.body, type_ignores=[]), include_attributes=False)
                block = hashlib.sha256(dump.encode('utf-8')).digest()
            definitions.append((stmt.name, stmt.lineno, bool(stmt.decorator_list), block))
    return declared, definitions


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
