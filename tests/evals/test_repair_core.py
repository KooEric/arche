import importlib.util
import shutil
from pathlib import Path

MODULE = Path(__file__).resolve().parents[2] / 'skills/arche-eval/scripts/repair.py'
spec = importlib.util.spec_from_file_location('repair_core', MODULE)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def test_broken_and_fixed_are_scored_separately(tmp_path, monkeypatch):
    candidate = tmp_path / 'candidate'
    shutil.copytree(repair.FIXTURE, candidate)
    monkeypatch.setattr(repair, 'browser_checks', lambda value: {'undo_restores_ready': not value, 'pickup': True})
    broken = repair.score(candidate)
    assert not broken['passed'] and not broken['execution_errors']
    html = candidate / 'index.html'
    html.write_text(html.read_text().replace('pickedUp = true; render(); };\nrender();', 'pickedUp = false; render(); };\nrender();'))
    fixed = repair.score(candidate)
    assert fixed['passed'] and fixed['changed_paths'] == ['index.html']
    (candidate / 'README.md').write_text('changed')
    assert not repair.score(candidate)['checks']['unrelated_paths_unchanged']


def test_untrusted_code_is_never_executed(tmp_path, monkeypatch):
    candidate = tmp_path / 'candidate'
    shutil.copytree(repair.FIXTURE, candidate)
    with (candidate / 'index.html').open('a') as file:
        file.write('<script>evil()</script>')
    monkeypatch.setattr(repair, 'browser_checks', lambda _: (_ for _ in ()).throw(AssertionError('must not run')))
    result = repair.score(candidate)
    assert not result['passed'] and not result['checks']['valid_candidate_grammar']
    assert not result['execution_errors']


def test_missing_browser_is_execution_error(tmp_path, monkeypatch):
    candidate = tmp_path / 'candidate'
    shutil.copytree(repair.FIXTURE, candidate)
    monkeypatch.setattr(repair, 'browser_checks', lambda _: (_ for _ in ()).throw(RuntimeError('browser unavailable')))
    result = repair.score(candidate)
    assert result['execution_errors'] == ['RuntimeError: browser unavailable']
    assert 'undo_restores_ready' not in result['checks']


def test_saved_run_replay_requires_provenance_and_success(tmp_path, monkeypatch):
    import json
    run = tmp_path / 'run'
    run.mkdir()
    candidate = run / 'candidate'
    shutil.copytree(repair.FIXTURE, candidate)
    html = candidate / 'index.html'
    html.write_text(html.read_text().replace('pickedUp = true; render(); };\nrender();', 'pickedUp = false; render(); };\nrender();'))
    baseline, after = repair.snapshot(repair.FIXTURE), repair.snapshot(candidate)
    hash_snapshot = lambda value: repair.digest(json.dumps(value, sort_keys=True).encode())
    record = {'schema_version': 1, 'task': 'pickup-undo',
              'execution': {'returncode': 0, 'timed_out': False, 'prompt': repair.PROMPT},
              'provenance': {'baseline': baseline, 'candidate': after, 'baseline_digest': hash_snapshot(baseline),
                             'candidate_digest': hash_snapshot(after), 'prompt_digest': repair.digest(repair.PROMPT.encode())}}
    (run / 'record.json').write_text(json.dumps(record))
    monkeypatch.setattr(repair, 'browser_checks', lambda value: {'undo': not value})
    assert repair.score_run(run)['passed']
    record['execution']['returncode'] = 1
    (run / 'record.json').write_text(json.dumps(record))
    assert not repair.score_run(run)['passed']
    record['execution']['returncode'] = 0
    (run / 'record.json').write_text(json.dumps(record))
    (candidate / 'README.md').write_text('tampered')
    assert 'candidate snapshot mismatch' in repair.score_run(run)['execution_errors']


def test_translation_repair_recognizes_safe_distinct_implementations(tmp_path):
    fixture = repair.case_fixture('translation-fallback')
    candidate = tmp_path / 'translation'
    shutil.copytree(fixture, candidate)
    path = candidate / 'translator.py'
    original = path.read_text()
    broken = repair.score(candidate, case='translation-fallback')
    assert not broken['passed'] and not broken['checks']['english_fallback']
    assert broken['checks']['empty_translation_preserved']
    for expression in ('translated.get(key, english.get(key, key))',
                       'translated[key] if key in translated else english.get(key, key)',
                       'english.get(key, key) if key not in translated else translated[key]'):
        path.write_text(original.replace('translated.get(key, key)', expression))
        assert repair.score(candidate, case='translation-fallback')['passed']
    path.write_text(original.replace('translated.get(key, key)', '__import__("os").system("false")'))
    assert not repair.score(candidate, case='translation-fallback')['checks']['valid_candidate_grammar']


def test_translation_replay_infers_case_and_variant(tmp_path):
    import json
    run = tmp_path / 'run'
    run.mkdir()
    fixture = repair.case_fixture('translation-fallback')
    candidate = run / 'candidate'
    shutil.copytree(fixture, candidate)
    path = candidate / 'translator.py'
    path.write_text(path.read_text().replace('translated.get(key, key)', 'translated.get(key, english.get(key, key))'))
    baseline, after = repair.snapshot(fixture), repair.snapshot(candidate)
    snap_digest = lambda value: repair.digest(json.dumps(value, sort_keys=True).encode())
    prompt = repair.case_prompt('translation-fallback', 'verify')
    record = {'schema_version': 1, 'task': 'translation-fallback', 'prompt_variant': 'verify',
              'execution': {'returncode': 0, 'timed_out': False, 'prompt': prompt},
              'provenance': {'baseline': baseline, 'candidate': after, 'baseline_digest': snap_digest(baseline),
                             'candidate_digest': snap_digest(after), 'prompt_digest': repair.digest(prompt.encode())}}
    (run / 'record.json').write_text(json.dumps(record))
    assert repair.score_run(run)['passed']
    record['prompt_variant'] = 'task-only'
    (run / 'record.json').write_text(json.dumps(record))
    assert not repair.score_run(run)['passed']


def test_snapshot_rejects_symlinks_fifos_and_oversized_files(tmp_path):
    import os
    import pytest
    candidate = tmp_path / 'candidate'
    candidate.mkdir()
    (candidate / 'link').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        repair.snapshot(candidate)
    (candidate / 'link').unlink()
    os.mkfifo(candidate / 'pipe')
    with pytest.raises(ValueError, match='special file'):
        repair.snapshot(candidate)
    (candidate / 'pipe').unlink()
    with (candidate / 'huge').open('wb') as stream:
        stream.truncate(repair.MAX_FILE_BYTES + 1)
    with pytest.raises(ValueError, match='size limit'):
        repair.snapshot(candidate)


def test_bounded_subprocess_timeout_and_output():
    import sys
    _, _, _, error = repair.run_bounded([sys.executable, '-c', 'import time; time.sleep(10)'], 0.1)
    assert error == 'model execution timed out'
    _, out, _, error = repair.run_bounded([sys.executable, '-c', 'print("x" * 2000000)'], 5)
    assert error == 'model output limit exceeded' and len(out) <= repair.MAX_OUTPUT_BYTES


def test_candidate_git_directory_is_detected(tmp_path):
    candidate = tmp_path / 'translation'
    shutil.copytree(repair.case_fixture('translation-fallback'), candidate)
    (candidate / '.git').mkdir()
    (candidate / '.git' / 'hidden').write_text('extra')
    assert not repair.score(candidate, case='translation-fallback')['checks']['unrelated_paths_unchanged']


def test_demonstration_replays_without_model():
    demo = Path(__file__).resolve().parents[1] / 'fixtures/repair-translation-demo'
    assert 'No model was run' in (demo / 'DEMONSTRATION.md').read_text()
    result = repair.score_run(demo)
    assert result['passed'] and not result['execution_errors']
