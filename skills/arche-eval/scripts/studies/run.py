#!/usr/bin/env python3
"""Run paired Codex trials on the bundled synthetic repositories only."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parents[2] / "arche"
sys.path.insert(0, str(HERE))
from score_tour import score_tour

PROMPT = '''Analyze this synthetic repository read-only. Use only the experiment skill at ../skill/SKILL.md if it exists; read and follow it, using its scripts directory. Otherwise inspect the sources directly without using any other skill. Do not execute repository code. {question}
Return only JSON with steps and edges arrays. For condition tasks include a top-level conditions array. Each step must have id in the form path.py:function and citations as an array of objects {{"path":"path.py","line":1}}. Each edge must have from, to, kind (use "calls" for a call). Cite the relevant source lines. Do not include markdown fences.'''


def load_manifest(path):
    data = json.loads(path.read_text())
    if data['arms'] != ['off', 'on'] or data['trials'] < 2 or len(data['cases']) < 2:
        raise ValueError('manifest requires off/on, at least two trials and two cases')
    if data['sandbox'] != 'read-only' or data['timeout_seconds'] <= 0:
        raise ValueError('read-only sandbox and positive timeout required')
    for case in data['cases']:
        repo = (HERE / case['repo']).resolve()
        if not repo.is_dir() or not repo.is_relative_to((HERE / 'fixtures').resolve()):
            raise ValueError('only bundled synthetic fixture directories are allowed')
        if any(p.is_symlink() for p in repo.rglob('*')):
            raise ValueError('fixture symlinks are not allowed')
    return data


def parse_events(stdout):
    message = None
    tokens = None
    for line in stdout.splitlines():
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError('event must be an object')
        if event.get('type') == 'item.completed':
            item = event.get('item')
            if not isinstance(item, dict):
                raise ValueError('completed item must be an object')
            if item.get('type') == 'agent_message':
                message = item.get('text')
        if event.get('type') == 'turn.completed':
            tokens = event.get('usage')
    if message is None:
        raise ValueError('missing final agent message')
    result = json.loads(message)
    if not isinstance(result, dict) or not isinstance(result.get('steps'), list) or not isinstance(result.get('edges'), list):
        raise ValueError('response requires steps and edges arrays')
    score_tour({'required_steps': [], 'expected_edges': []}, result)
    for step in result['steps']:
        citations = step.get('citations', [])
        if not isinstance(citations, list) or any(not isinstance(c, dict) or not isinstance(c.get('path'), str) or type(c.get('line')) is not int for c in citations):
            raise ValueError('citations must contain path and line')
    conditions = result.get('conditions', [])
    if not isinstance(conditions, list) or any(not isinstance(c, dict) or not isinstance(c.get('id'), str) or not isinstance(c.get('expression'), str) for c in conditions):
        raise ValueError('conditions must contain id and expression')
    return result, tokens


def citation_score(steps, repo):
    valid = total = 0
    for step in steps:
        if not isinstance(step, dict):
            continue
        for citation in step.get('citations', []):
            total += 1
            try:
                path = (repo / citation['path']).resolve()
                line = citation['line']
                if path.is_relative_to(repo.resolve()) and path.is_file() and isinstance(line, int) and not isinstance(line, bool) and 1 <= line <= len(path.read_text().splitlines()):
                    valid += 1
            except (KeyError, TypeError, OSError, UnicodeError):
                pass
    return {'valid': valid, 'total': total, 'accuracy': valid / total if total else None}


def run_trial(manifest, case, arm, trial, output, prompt_template=PROMPT, codex="codex"):
    with tempfile.TemporaryDirectory(prefix='arche-eval-') as temp:
        repo = Path(temp) / 'repo'
        shutil.copytree(HERE / case['repo'], repo)
        if arm == 'on':
            shutil.copytree(SKILL, Path(temp) / 'skill', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        prompt = prompt_template.format(question=case['question'])
        cmd = [codex, 'exec', '--json', '--ephemeral', '--skip-git-repo-check', '--ignore-user-config', '--sandbox', manifest['sandbox'], '--model', manifest['model'], '--cd', str(repo), prompt]
        start = time.monotonic()
        record = {'case_id': case['id'], 'arm': arm, 'model': manifest['model'], 'trial': trial, 'steps': [], 'edges': [], 'tokens': None, 'cost': None}
        trace = output / f"{case['id']}-{arm}-{trial}.jsonl"
        try:
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=manifest['timeout_seconds'])
            trace.write_text(completed.stdout, encoding='utf-8')
            (trace.with_suffix('.stderr.txt')).write_text(completed.stderr, encoding='utf-8')
            if completed.returncode:
                record.update(status='execution_error', error=f'codex exit {completed.returncode}')
            else:
                try:
                    result, record['tokens'] = parse_events(completed.stdout)
                    record.update(steps=result['steps'], edges=result['edges'], conditions=result.get('conditions', []), status='ok')
                    record['citations'] = citation_score(result['steps'], repo)
                except (ValueError, TypeError, KeyError) as exc:
                    record.update(status='response_error', error=str(exc))
        except subprocess.TimeoutExpired as exc:
            trace.write_bytes(exc.stdout if isinstance(exc.stdout, bytes) else (exc.stdout or '').encode())
            record.update(status='timeout', error='time budget exceeded')
        except OSError as exc:
            record.update(status='execution_error', error=str(exc))
        record['latency_seconds'] = round(time.monotonic() - start, 3)
        record['trace'] = trace.name if trace.exists() else None
        return record


def table(records, golden):
    cases = {c['id']: c for c in golden['cases']}
    lines = ['| Case | Arm | Trial | Status | Step recall | Extra edges | Citation validity | Cost | Seconds |', '|---|---|---:|---|---:|---:|---:|---:|---:|']
    for r in records:
        recall = extra = cite = '—'
        if r['status'] == 'ok':
            try:
                result = score_tour(cases[r['case_id']], r)
                recall, extra = f"{result['required_step_recall']:.2f}", str(len(result['extra_edges']))
                c = r['citations']
                cite = f"{c['valid']}/{c['total']}" if c['total'] else '—'
            except (KeyError, TypeError, ValueError):
                r['status'] = 'response_error'
                r['error'] = 'invalid steps or edges for scoring'
        lines.append(f"| {r['case_id']} | {r['arm']} | {r['trial']} | {r['status']} | {recall} | {extra} | {cite} | {'—' if r['cost'] is None else r['cost']} | {r['latency_seconds']:.3f} |")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=HERE / 'manifest.json')
    parser.add_argument('--output', type=Path, required=True, help='new output directory outside fixture repositories')
    parser.add_argument('--codex', default='codex', help='Codex executable or absolute path')
    parser.add_argument('--cases', nargs='+', help='restrict to named manifest cases')
    parser.add_argument('--model', help='override model for a controlled paired run')
    parser.add_argument('--prompt-file', type=Path, help='UTF-8 prompt template containing {question}')
    parser.add_argument('--dry-run', action='store_true', help='print planned trials only; no model calls or measured results')
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    if args.model:
        manifest['model'] = args.model
    prompt_template = args.prompt_file.read_text(encoding='utf-8') if args.prompt_file else PROMPT
    if '{question}' not in prompt_template:
        parser.error('prompt template must contain {question}')
    if args.cases:
        unknown = set(args.cases) - {c['id'] for c in manifest['cases']}
        if unknown:
            parser.error('unknown case IDs: ' + ', '.join(sorted(unknown)))
        manifest['cases'] = [c for c in manifest['cases'] if c['id'] in args.cases]
    plan = [(c, a, n) for c in manifest['cases'] for n in range(1, manifest['trials'] + 1) for a in manifest['arms']]
    if args.dry_run:
        print(json.dumps({'dry_run': True, 'model': manifest['model'], 'timeout_seconds': manifest['timeout_seconds'], 'sandbox': manifest['sandbox'], 'trials': [{'case_id': c['id'], 'arm': a, 'trial': n} for c, a, n in plan]}, indent=2))
        return
    out = args.output.resolve()
    if out.is_relative_to((HERE / 'fixtures').resolve()) or out.exists():
        parser.error('output must be a new directory outside fixtures')
    out.mkdir(parents=True)
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    golden_text = (HERE / 'golden.json').read_text()
    (out / 'golden.json').write_text(golden_text)
    inputs = [(SKILL, 'skill/arche'), (HERE / 'fixtures', 'studies/fixtures')]
    hashes = {prefix + '/' + p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for base, prefix in inputs for p in base.rglob('*') if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    executable = shutil.which(args.codex)
    hashes['experiment/prompt-template'] = hashlib.sha256(prompt_template.encode()).hexdigest()
    hashes['experiment/manifest'] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    hashes['experiment/golden'] = hashlib.sha256(golden_text.encode()).hexdigest()
    for case in manifest['cases']:
        hashes['prompt/' + case['id']] = hashlib.sha256(prompt_template.format(question=case['question']).encode()).hexdigest()
    for p in [Path(__file__), HERE / 'harness_score.py', HERE / 'score_tour.py']:
        hashes['studies/' + p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    if executable and Path(executable).is_file():
        hashes['tool/codex'] = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
    else:
        hashes['tool/codex'] = None
    (out / 'prompt-template.txt').write_text(prompt_template, encoding='utf-8')
    (out / 'input-hashes.json').write_text(json.dumps(hashes, indent=2) + '\n')
    records = []
    for c, a, n in plan:
        record = run_trial(manifest, c, a, n, out, prompt_template, args.codex)
        records.append(record)
        table([record], json.loads(golden_text))
        with (out / 'runs.jsonl').open('a') as stream:
            stream.write(json.dumps(record) + '\n')
    summary = table(records, json.loads(golden_text))
    (out / 'table.md').write_text(summary)
    print(summary)


if __name__ == '__main__':
    main()
