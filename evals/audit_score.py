#!/usr/bin/env python3
"""Score audit rules on a deterministic synthetic example, not production data."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/arche/scripts'))
from arche_lib.audit import build_audit, finding_key  # noqa: E402


def score():
    body = '    a = 1\n    b = 2\n    c = a + b\n    return c\n'
    sources = {
        'internal/api.py': '__all__ = ["unused", "used"]\ndef unused():\n    pass\ndef used():\n    pass\n',
        'consumer.py': 'from internal.api import used\n',
        'private.py': 'def _unused():\n    pass\ndef _used():\n    pass\n_used()\n',
        'a.py': 'def one():\n' + body,
        'b.py': 'def two():\n' + body,
        'tests/test_normal.py': 'def _helper():\n    pass\n',
        '__init__.py': 'def _hook():\n    pass\n',
    }
    # Known import links leave private.py isolated and suppress isolation for
    # the other source files. Tests and package entry points are protected.
    edges = [{'kind': 'IMPORTS', 'from': 'file:' + p, 'to': 'file:consumer.py'}
             for p in ('internal/api.py', 'a.py', 'b.py')]
    graph = {'nodes': [{'id': 'file:' + p, 'type': 'file', 'lang': 'python'} for p in sources],
             'edges': edges, 'skipped': []}
    findings = build_audit(graph, sources.get, ['internal'])
    expected = {
        ('unused-private-symbol', 'private.py', '_unused'),
        ('unused-internal-export', 'internal/api.py', 'unused'),
        ('isolated-module', 'private.py', ''),
        ('duplicate-block', (('a.py', 'one'), ('b.py', 'two'))),
    }
    actual = {finding_key(f) for f in findings}
    metrics = {}
    for rule in sorted({key[0] for key in expected | actual}):
        truth = {key for key in expected if key[0] == rule}
        predicted = {key for key in actual if key[0] == rule}
        tp, fp, fn = len(truth & predicted), len(predicted - truth), len(truth - predicted)
        metrics[rule] = {'tp': tp, 'fp': fp, 'fn': fn,
                         'precision': tp / (tp + fp) if tp + fp else None,
                         'recall': tp / (tp + fn) if tp + fn else None}
    return {'scope': 'synthetic fixture; candidate matching, not production precision or severity', 'rules': metrics}


if __name__ == '__main__':
    print(json.dumps(score(), sort_keys=True, indent=2))
