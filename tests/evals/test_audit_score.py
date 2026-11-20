import importlib.util
from pathlib import Path


def test_synthetic_audit_metrics_cover_every_rule():
    path = Path(__file__).resolve().parents[2] / 'evals/audit_score.py'
    spec = importlib.util.spec_from_file_location('audit_score', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.score()
    assert len(result['rules']) == 4
    assert all(m == {'tp': 1, 'fp': 0, 'fn': 0, 'precision': 1.0, 'recall': 1.0} for m in result['rules'].values())
