import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'skills/arche-eval/scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('evaluation_report', SCRIPTS / 'evaluation_report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def archive(tmp_path):
    path = tmp_path / 'archive'
    path.mkdir()
    golden = json.loads((report.STUDIES / 'golden.json').read_text())
    case = golden['cases'][0]
    (path / 'golden.json').write_text(json.dumps({'cases': [case]}))
    (path / 'manifest.json').write_text(json.dumps({'cases': [{'id': case['id']}], 'arms': ['off'], 'trials': 1}))
    (path / 'input-hashes.json').write_text(json.dumps({'experiment/golden': hashlib.sha256((path / 'golden.json').read_bytes()).hexdigest()}))
    run = {'case_id': case['id'], 'arm': 'off', 'trial': 1, 'model': 'test', 'status': 'ok',
           'steps': [{'id': claim, 'citations': [{'path': next(iter(src)), 'line': next(iter(src.values()))[0]}]}
                     for claim, src in case['citation_lines'].items()], 'edges': case['expected_edges']}
    return path, run


def save(path, run):
    (path / 'runs.jsonl').write_text(json.dumps(run) + '\n')


def test_study_outcomes_and_incomplete(tmp_path):
    path, run = archive(tmp_path)
    save(path, run)
    assert report.replay(path)[0]['outcome'] == 'passed'
    run['edges'] = []
    save(path, run)
    assert report.replay(path)[0]['outcome'] == 'failed'
    run['steps'][0]['citations'] = []
    save(path, run)
    assert report.replay(path)[0]['outcome'] == 'support_error'
    run['status'] = 'error'
    save(path, run)
    assert report.replay(path)[0]['outcome'] == 'execution_error'
    run['status'] = 'ok'
    run['steps'] = 'malformed'
    save(path, run)
    assert report.replay(path)[0]['outcome'] == 'format_error'
    (path / 'runs.jsonl').write_text('')
    with pytest.raises(ValueError, match='incomplete'):
        report.replay(path)


def test_hash_mismatch_and_legacy_replay(tmp_path):
    path, run = archive(tmp_path)
    save(path, run)
    (path / 'input-hashes.json').write_text(json.dumps({'experiment/golden': 'wrong'}))
    with pytest.raises(ValueError, match='golden hash'):
        report.replay(path)
    (path / 'input-hashes.json').write_text('{}')
    assert report.replay(path)[0]['outcome'] == 'passed'


def test_escaping_and_per_trial_line_anchors(tmp_path):
    path, run = archive(tmp_path)
    save(path, run)
    row = report.replay(path)[0]
    row['evidence'] = {'main.py': '<script>bad()</script>\nhello'}
    row['references'] = [{'claim': '<img>', 'file': 'main.py', 'line': 1}]
    other = dict(row, case='other')
    out = tmp_path / 'report'
    report.write_report(out, [row, other])
    html = (out / 'report.html').read_text()
    assert '<script>bad()' not in html and '&lt;script&gt;' in html and '&lt;img&gt;' in html
    assert 'href="#evidence-1-0-line-1"' in html and 'href="#evidence-2-0-line-1"' in html


def test_malformed_repair_capture_and_rejected_diff(tmp_path):
    candidate = tmp_path / 'bad'
    candidate.mkdir()
    (candidate / 'record.json').write_text('{}')
    assert report.replay(candidate)[0]['outcome'] == 'execution_error'
    demo = Path(__file__).resolve().parents[1] / 'fixtures/repair-translation-demo'
    copy = tmp_path / 'demo'
    shutil.copytree(demo, copy)
    file = copy / 'candidate' / 'translator.py'
    file.write_text(file.read_text().replace('translated.get(key, english.get(key, key))', '"<script>"'))
    record = json.loads((copy / 'record.json').read_text())
    after = report.repair.snapshot(copy / 'candidate')
    record['provenance']['candidate'] = after
    record['provenance']['candidate_digest'] = report.repair.digest(json.dumps(after, sort_keys=True).encode())
    (copy / 'record.json').write_text(json.dumps(record))
    row = report.replay(copy)[0]
    assert row['outcome'] == 'failed' and '<script>' in row['evidence']['translator.py']
