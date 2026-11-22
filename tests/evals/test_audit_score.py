import importlib.util
from pathlib import Path


def test_synthetic_audit_metrics_cover_every_rule():
    path = Path(__file__).resolve().parents[2] / 'evals/audit_score.py'
    spec = importlib.util.spec_from_file_location('audit_score', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.score()
    assert len(result['rules']) == 6
    assert all(m['fp'] == 0 and m['fn'] == 0 and m['precision'] == 1.0 and m['recall'] == 1.0 for m in result['rules'].values())
    assert result['rules']['unused-private-member']['tp'] == 3 and result['rules']['unused-nonpublic-type']['tp'] == 2
