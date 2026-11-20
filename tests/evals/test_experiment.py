import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

PATH = Path(__file__).resolve().parents[2] / 'evals/experiments/run.py'
spec = importlib.util.spec_from_file_location('experiment', PATH)
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


def test_mock_trials_and_failures(tmp_path):
    manifest = experiment.load_manifest(experiment.HERE / 'manifest.json')
    case = manifest['cases'][0]
    answer = {'steps': [{'id': 'main.py:main', 'citations': [{'path': 'main.py', 'line': 4}]}], 'edges': []}
    events = '\n'.join(json.dumps(x) for x in [
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(answer)}},
        {'type': 'turn.completed', 'usage': {'input_tokens': 5, 'output_tokens': 7}},
    ])
    commands = []
    def fake(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, events, '')
    with mock.patch.object(experiment.subprocess, 'run', side_effect=fake):
        off = experiment.run_trial(manifest, case, 'off', 1, tmp_path)
        on = experiment.run_trial(manifest, case, 'on', 1, tmp_path)
    assert off['status'] == on['status'] == 'ok'
    assert off['citations'] == {'valid': 1, 'total': 1, 'accuracy': 1.0}
    assert commands[0][-1] == commands[1][-1]
    assert commands[0][:-2] == commands[1][:-2]
    assert off['cost'] is None and off['tokens']['input_tokens'] == 5
    with mock.patch.object(experiment.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'failed')):
        failed = experiment.run_trial(manifest, case, 'off', 2, tmp_path)
    assert failed['status'] == 'execution_error'
    with mock.patch.object(experiment.subprocess, 'run', side_effect=subprocess.TimeoutExpired('codex', 1)):
        assert experiment.run_trial(manifest, case, 'on', 2, tmp_path)['status'] == 'timeout'
    golden = json.loads((experiment.HERE / 'golden.json').read_text())
    result = experiment.table([off, failed], golden)
    assert '| dispatch | off | 1 | ok | 0.33 |' in result
    assert '| dispatch | off | 2 | execution_error | — |' in result


def test_citations_and_reject_external_fixture(tmp_path):
    repo = experiment.HERE / 'fixtures/dispatch'
    result = experiment.citation_score([{'citations': [{'path': '../receipt/cli.py', 'line': 1}, {'path': 'main.py', 'line': 999}]}], repo)
    assert result == {'valid': 0, 'total': 2, 'accuracy': 0.0}
    manifest = json.loads((experiment.HERE / 'manifest.json').read_text())
    manifest['cases'][0]['repo'] = '../../..'
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest))
    import pytest
    with pytest.raises(ValueError):
        experiment.load_manifest(path)


def test_alternate_model_and_prompt_are_paired(tmp_path):
    prompt = tmp_path / 'prompt.txt'
    prompt.write_text('Inspect: {question}')
    result = subprocess.run([sys.executable, str(PATH), '--dry-run', '--output', str(tmp_path / 'out'), '--model', 'alternate', '--prompt-file', str(prompt)], capture_output=True, text=True, check=True)
    plan = json.loads(result.stdout)
    assert plan['model'] == 'alternate'
    for case in {item['case_id'] for item in plan['trials']}:
        assert {item['arm'] for item in plan['trials'] if item['case_id'] == case} == {'off', 'on'}


def test_installed_study_uses_sibling_skill_and_selected_case(tmp_path):
    import shutil
    skills = tmp_path / 'installed'
    shutil.copytree(experiment.HERE.parents[2] / 'arche', skills / 'arche', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(experiment.HERE.parents[1], skills / 'arche-eval', ignore=shutil.ignore_patterns('__pycache__'))
    fake = tmp_path / 'fake-codex'
    fake.write_text('#!/usr/bin/env python3\nimport json\nprint(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps({"steps":[],"edges":[]})}}))\n')
    fake.chmod(0o755)
    runner = skills / 'arche-eval/scripts/studies/run.py'
    output = tmp_path / 'output'
    subprocess.run([sys.executable, str(runner), '--cases', 'dispatch', '--codex', str(fake), '--output', str(output)], check=True, capture_output=True, text=True)
    runs = [json.loads(line) for line in (output / 'runs.jsonl').read_text().splitlines()]
    assert len(runs) == 4 and {r['case_id'] for r in runs} == {'dispatch'}
    assert {r['arm'] for r in runs} == {'off', 'on'} and all(r['status'] == 'ok' for r in runs)
    assert json.loads((output / 'input-hashes.json').read_text())['tool/codex']
    score = skills / 'arche-eval/scripts/studies/harness_score.py'
    subprocess.run([sys.executable, str(score), str(output / 'golden.json'), str(output / 'runs.jsonl')], check=True, capture_output=True, text=True)


def test_malformed_response_is_recorded_and_remaining_trials_continue(tmp_path):
    fake = tmp_path / 'codex'
    fake.write_text('#!/usr/bin/env python3\nimport json\nprint(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps({"steps":[{"id":"a","citations":None}],"edges":[]})}}))\n')
    fake.chmod(0o755)
    output = tmp_path / 'run'
    subprocess.run([sys.executable, str(PATH), '--cases', 'dispatch', '--codex', str(fake), '--output', str(output)], check=True, capture_output=True, text=True)
    runs = [json.loads(line) for line in (output / 'runs.jsonl').read_text().splitlines()]
    assert len(runs) == 4
    assert all(run['status'] == 'response_error' for run in runs)
