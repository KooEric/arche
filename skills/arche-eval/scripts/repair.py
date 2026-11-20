#!/usr/bin/env python3
"""Run and score the isolated pickup repair task. Requires Python Playwright + Chromium for scoring."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures' / 'pickup'
PICKUP_PROMPT = ('Fix the pickup queue bug described in README.md. Work only in this repository. '
          'Preserve the application and change only the boolean assigned in the Undo pickup handler in index.html. Do not access the network. '
          'Explain the change and any tests you ran.')

TRANSLATION_PROMPT = ('Fix the translation fallback bug described in README.md. Work only in this repository. '
                      'Preserve the application and change only the return expression in translator.py. '
                      'Do not access the network. Explain the change and any tests you ran.')
CASES = {'pickup-undo': ('pickup', PICKUP_PROMPT), 'translation-fallback': ('translation', TRANSLATION_PROMPT)}
PROMPT = PICKUP_PROMPT  # Backward-compatible public constant.


def case_prompt(case, variant):
    prompt = CASES[case][1]
    if variant == 'verify':
        prompt += ' Verify the fix with relevant tests and report their results.'
    elif variant != 'task-only':
        raise ValueError('unknown prompt variant')
    return prompt


def case_fixture(case):
    return ROOT / 'fixtures' / CASES[case][0]


def digest(data):
    return hashlib.sha256(data).hexdigest()


MAX_FILES = 1000
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024


def snapshot(path, ignore_git=False):
    """Hash bounded regular files; never follow candidate symlinks or open special files."""
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError('snapshot root must be a real directory')
    result, count, total = {}, 0, 0
    for directory, dirs, files in os.walk(path, followlinks=False):
        dirs.sort()
        files.sort()
        for name in dirs + files:
            entry = Path(directory) / name
            rel = entry.relative_to(path).as_posix()
            if ignore_git and rel == '.git':
                continue
            count += 1
            if count > MAX_FILES:
                raise ValueError('snapshot entry limit exceeded')
            info = entry.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ValueError(f'symlink forbidden: {rel}')
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f'special file forbidden: {rel}')
            if info.st_size > MAX_FILE_BYTES or total + info.st_size > MAX_TOTAL_BYTES:
                raise ValueError('snapshot size limit exceeded')
            fd = os.open(entry, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_FILE_BYTES:
                    raise ValueError(f'unsafe file: {rel}')
                data = stream.read(MAX_FILE_BYTES + 1)
            total += len(data)
            if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError('snapshot size limit exceeded')
            result[rel] = {'type': 'file', 'sha256': digest(data), 'size': len(data)}
        if ignore_git and '.git' in dirs and Path(directory) == path:
            dirs.remove('.git')
    return result


def run_bounded(cmd, timeout):
    """Bound child output and kill its process group on deadline or excessive output."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    selector = selectors.DefaultSelector()
    for stream in (proc.stdout, proc.stderr):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    chunks = {proc.stdout: bytearray(), proc.stderr: bytearray()}
    deadline = time.monotonic() + timeout
    error = None
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                error = 'model execution timed out'
                break
            for key, _ in selector.select(min(remaining, 0.2)):
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                chunks[key.fileobj].extend(data)
                if len(chunks[key.fileobj]) > MAX_OUTPUT_BYTES:
                    error = 'model output limit exceeded'
                    break
            if error:
                break
        if error:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=max(0.1, deadline - time.monotonic()) if not error else 2)
        except subprocess.TimeoutExpired:
            error = 'model execution timed out'
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=2)
        return proc.returncode, bytes(chunks[proc.stdout][:MAX_OUTPUT_BYTES]), bytes(chunks[proc.stderr][:MAX_OUTPUT_BYTES]), error
    finally:
        selector.close()
        proc.stdout.close()
        proc.stderr.close()


def git_env():
    env = os.environ.copy()
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_ATTR_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0')
    for key in list(env):
        if key.startswith('GIT_CONFIG_KEY_') or key.startswith('GIT_CONFIG_VALUE_') or key in ('GIT_CONFIG_COUNT', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_EXTERNAL_DIFF'):
            env.pop(key, None)
    return env


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def run(args):
    out = Path(args.output).resolve()
    if out == ROOT or ROOT in out.parents:
        raise ValueError('output must be outside the installed skill')
    out.mkdir(parents=True, exist_ok=False)
    fixture = case_fixture(args.case)
    prompt = case_prompt(args.case, args.prompt_variant)
    record = {'schema_version': 1, 'task': args.case, 'prompt_variant': args.prompt_variant, 'execution': {}, 'provenance': {}}
    try:
        with tempfile.TemporaryDirectory(prefix='arche-repair-') as tmp:
            repo = Path(tmp) / 'repo'
            shutil.copytree(fixture, repo)
            before = snapshot(repo)
            subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, 'init', '-q', str(repo)], check=True, capture_output=True, timeout=30, env=git_env())
            subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, '-C', str(repo), 'add', '.'], check=True, capture_output=True, timeout=30, env=git_env())
            cmd = [args.codex, 'exec', '--ephemeral', '--sandbox', 'workspace-write', '--cd', str(repo), '--skip-git-repo-check', '--json']
            if args.model:
                cmd += ['--model', args.model]
            cmd += [prompt]
            record['execution']['command'] = cmd[:-1] + ['<prompt>']
            record['execution']['prompt'] = prompt
            record['execution']['model'] = args.model
            started = time.monotonic()
            try:
                returncode, stdout, stderr, error = run_bounded(cmd, args.timeout)
                record['execution'].update(returncode=returncode, timed_out=error == 'model execution timed out')
                if error:
                    record['execution']['error'] = error
                events = []
                for line in stdout.decode('utf-8', errors='replace').splitlines():
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
                record['execution']['usage'] = [e['usage'] for e in events if isinstance(e, dict) and 'usage' in e]
                (out / 'stdout.txt').write_bytes(stdout)
                (out / 'stderr.txt').write_bytes(stderr)
            except OSError as e:
                record['execution'].update(returncode=None, timed_out=False, error=str(e))
            record['execution']['duration_seconds'] = round(time.monotonic() - started, 3)
            after = snapshot(repo, ignore_git=True)
            record['provenance'] = {'baseline': before, 'candidate': after, 'baseline_digest': digest(json.dumps(before, sort_keys=True).encode()), 'candidate_digest': digest(json.dumps(after, sort_keys=True).encode()), 'prompt_digest': digest(prompt.encode())}
            # Copy without following links; candidate content is never executed by the scorer.
            shutil.copytree(repo, out / 'candidate', ignore=shutil.ignore_patterns('.git'), symlinks=True)
            diff = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, 'diff', '--no-index', '--no-ext-diff', '--no-textconv', '--binary', '--', str(fixture), str(out / 'candidate')], capture_output=True, timeout=30, env=git_env(), cwd=tmp)
            (out / 'change.patch').write_bytes(diff.stdout)
            record['execution']['diff_returncode'] = diff.returncode
            record['execution']['untracked'] = sorted(set(after) - set(before))
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        record['execution']['error'] = str(e)
    write_json(out / 'record.json', record)
    return 0 if record['execution'].get('returncode') == 0 and not record['execution'].get('error') else 2


def browser_checks(undo_value, browser_executable=None):
    # Only trusted fixture code runs. Candidate is strictly parsed data, never HTML or JS.
    from playwright.sync_api import sync_playwright
    html = (FIXTURE / 'index.html').read_text(encoding='utf-8')
    html = html.replace('pickedUp = true; render(); };\nrender();', 'pickedUp = ' + str(undo_value).lower() + '; render(); };\nrender();')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, **({'executable_path': browser_executable} if browser_executable else {}))
        try:
            context = browser.new_context(service_workers='block')
            context.route('**/*', lambda route: route.abort())
            page = context.new_page()
            page.set_content(html, wait_until='load')
            def state(prefix, expected_status, pickup_visible, undo_visible):
                return {prefix + '_status': page.locator('#status').inner_text() == expected_status,
                        prefix + '_pickup_visible': page.locator('#pickup').is_visible() == pickup_visible,
                        prefix + '_undo_visible': page.locator('#undo').is_visible() == undo_visible}
            checks = state('initial', 'Ready for pickup', True, False)
            if page.locator('#pickup').is_visible():
                page.locator('#pickup').click()
            checks.update(state('picked', 'Picked up', False, True))
            if page.locator('#undo').is_visible():
                page.locator('#undo').click()
            checks.update(state('undone', 'Ready for pickup', True, False))
            # Do not wait on an intentionally hidden control after a failed transition.
            if page.locator('#pickup').is_visible():
                page.locator('#pickup').click()
                checks.update(state('repeat_picked', 'Picked up', False, True))
                if page.locator('#undo').is_visible():
                    page.locator('#undo').click()
                checks.update(state('repeat_undone', 'Ready for pickup', True, False))
            else:
                checks.update({key: False for key in ('repeat_picked_status', 'repeat_picked_pickup_visible',
                               'repeat_picked_undo_visible', 'repeat_undone_status',
                               'repeat_undone_pickup_visible', 'repeat_undone_undo_visible')})
            context.close()
            return checks
        finally:
            browser.close()


def score(candidate, browser_executable=None, case="pickup-undo"):
    result = {'schema_version': 1, 'task': case, 'execution_errors': [], 'checks': {}, 'passed': False}
    try:
        fixture = case_fixture(case)
        baseline, actual = snapshot(fixture), snapshot(candidate)
        changed = sorted(k for k in baseline.keys() | actual.keys() if baseline.get(k) != actual.get(k))
        result['changed_paths'] = changed
        editable = 'index.html' if case == 'pickup-undo' else 'translator.py'
        result['checks']['unrelated_paths_unchanged'] = all(k == editable for k in changed)
        if case == 'translation-fallback':
            result['checks'].update(translation_checks(candidate, fixture))
            result['passed'] = all(result['checks'].values())
            return result
        path = candidate / 'index.html'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
            result['checks']['valid_candidate_grammar'] = False
            return result
        original = (FIXTURE / 'index.html').read_text(encoding='utf-8')
        marker = 'pickedUp = true; render(); };\nrender();'
        if original.count(marker) != 1:
            raise RuntimeError('fixture grammar is ambiguous')
        candidate_text = path.read_text(encoding='utf-8')
        accepted = {original.replace(marker, 'pickedUp = ' + value + '; render(); };\nrender();'): value == 'true' for value in ('true', 'false')}
        if candidate_text not in accepted:
            result['checks']['valid_candidate_grammar'] = False
            return result
        result['checks']['valid_candidate_grammar'] = True
        result['checks'].update(browser_checks(accepted[candidate_text], browser_executable) if browser_executable else browser_checks(accepted[candidate_text]))
    except Exception as e:
        result['execution_errors'].append(f'{type(e).__name__}: {e}')
    result['passed'] = not result['execution_errors'] and bool(result['checks']) and all(result['checks'].values())
    return result


def translation_checks(candidate, fixture):
    """Recognize exact safe expressions, then evaluate their semantics as data, never as code."""
    path = candidate / 'translator.py'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        return {'valid_candidate_grammar': False}
    original = (fixture / 'translator.py').read_text(encoding='utf-8')
    marker = 'translated.get(key, key)'
    if original.count(marker) != 1:
        raise RuntimeError('fixture grammar is ambiguous')
    expressions = {
        'translated.get(key, key)': 'broken',
        'translated.get(key, english.get(key, key))': 'fallback',
        'translated[key] if key in translated else english.get(key, key)': 'fallback',
        'english.get(key, key) if key not in translated else translated[key]': 'fallback',
    }
    text = path.read_text(encoding='utf-8')
    modes = {original.replace(marker, expr): mode for expr, mode in expressions.items()}
    if text not in modes:
        return {'valid_candidate_grammar': False}
    mode = modes[text]
    catalogs = {'en': {'ready': 'Ready for pickup', 'undo': 'Undo pickup', 'empty': ''},
                'ko': {'ready': '수령 준비'}, 'fr': {'ready': 'Prêt'}}
    def translate(locale, key):
        local = catalogs.get(locale, {})
        return local.get(key, catalogs['en'].get(key, key) if mode == 'fallback' else key)
    return {'valid_candidate_grammar': True,
            'localized_value': translate('ko', 'ready') == '수령 준비',
            'other_locale_isolated': translate('fr', 'ready') == 'Prêt',
            'english_fallback': translate('ko', 'undo') == 'Undo pickup',
            'unknown_locale_fallback': translate('xx', 'undo') == 'Undo pickup',
            'unknown_key_fallback': translate('ko', 'missing') == 'missing',
            'empty_translation_preserved': translate('en', 'empty') == ''}


def score_run(run_dir, browser_executable=None):
    """Replay a saved run, requiring its captured snapshots and successful execution."""
    try:
        record_path = run_dir / 'record.json'
        if run_dir.is_symlink() or record_path.is_symlink() or not record_path.is_file() or record_path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('run record must be a bounded regular file')
        fd = os.open(record_path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('run record must be a regular file')
            record = json.loads(stream.read(MAX_FILE_BYTES + 1))
        provenance = record['provenance']
        case = record['task']
        fixture = case_fixture(case)
        variant = record.get('prompt_variant', 'task-only')
        prompt = case_prompt(case, variant)
        baseline = snapshot(fixture)
        candidate = snapshot(run_dir / 'candidate')
        baseline_digest = digest(json.dumps(baseline, sort_keys=True).encode())
        errors = []
        if record.get('schema_version') != 1 or case not in CASES:
            errors.append('run record schema or task mismatch')
        if provenance.get('baseline') != baseline or provenance.get('baseline_digest') != baseline_digest:
            errors.append('baseline snapshot or digest mismatch')
        if provenance.get('candidate') != candidate or provenance.get('candidate_digest') != digest(json.dumps(candidate, sort_keys=True).encode()):
            errors.append('candidate snapshot mismatch')
        if provenance.get('prompt_digest') != digest(prompt.encode()) or record['execution'].get('prompt') != prompt:
            errors.append('prompt provenance mismatch')
        execution = record['execution']
        if execution.get('returncode') != 0 or execution.get('timed_out') is not False or execution.get('error'):
            errors.append('model execution did not complete successfully')
        result = score(run_dir / 'candidate', browser_executable, case)
        result['execution_errors'] = errors + result['execution_errors']
        result['passed'] = result['passed'] and not errors
        return result
    except (OSError, ValueError, KeyError, TypeError) as e:
        return {'schema_version': 1, 'task': 'pickup-undo', 'execution_errors': [f'invalid run record: {e}'],
                'checks': {}, 'passed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    r = sub.add_parser('run')
    r.add_argument('--output', required=True, help='new output directory')
    r.add_argument('--codex', default='codex')
    r.add_argument('--model')
    r.add_argument('--timeout', type=int, default=600)
    r.add_argument('--case', choices=CASES, default='pickup-undo')
    r.add_argument('--prompt-variant', choices=['task-only', 'verify'], default='task-only')
    s = sub.add_parser('score')
    target = s.add_mutually_exclusive_group(required=True)
    target.add_argument('--candidate', type=Path)
    target.add_argument('--run', type=Path, help='replay and verify a captured run')
    s.add_argument('--browser-executable', default=os.environ.get('ARCHE_EVAL_BROWSER_EXECUTABLE'), help='path to a local Chromium or Chrome executable')
    s.add_argument('--case', choices=CASES, default='pickup-undo', help='case for --candidate; --run infers it')
    s.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'run':
        return run(args)
    output_path = Path(args.output).resolve()
    input_path = (args.run or args.candidate).resolve()
    if output_path == ROOT or ROOT in output_path.parents or output_path == input_path or input_path in output_path.parents:
        parser.error('score output must be outside the installed skill and input directory')
    result = score_run(args.run, args.browser_executable) if args.run else score(args.candidate, args.browser_executable, args.case)
    write_json(output_path, result)
    return 0 if result['passed'] else 2

if __name__ == '__main__':
    sys.exit(main())
