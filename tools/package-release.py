#!/usr/bin/env python3
"""Build reproducible skill archives from an existing local version tag."""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r'v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*))?\Z')
SKILLS = ('arche', 'arche-eval')


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def archive(tag, skill, destination):
    prefix = 'skills/' + skill + '/'
    entries = git('ls-tree', '-r', '-z', tag, '--', prefix).split(b'\0')
    files = []
    for entry in filter(None, entries):
        metadata, path_bytes = entry.split(b'\t', 1)
        mode, kind, oid = metadata.decode('ascii').split()
        path = path_bytes.decode('utf-8')
        if mode not in ('100644', '100755') or kind != 'blob' or not path.startswith(prefix):
            raise ValueError('release contains unsupported entry: ' + path)
        relative = path[len(prefix):]
        if not relative or any(part in ('', '.', '..') for part in relative.split('/')):
            raise ValueError('unsafe path in release: ' + path)
        parts = relative.lower().split('/')
        if any(part.startswith('.') or part in ('__pycache__', 'node_modules', 'runs', 'traces') for part in parts) or relative.lower().endswith(('.pyc', '.pem', '.key', '.p12', '.env')):
            raise ValueError('private or generated path in release: ' + path)
        files.append((relative, mode, oid))
    if not any(path == 'SKILL.md' for path, _, _ in files):
        raise ValueError(skill + ' is missing SKILL.md at tag')
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for relative, mode, oid in sorted(files):
            info = zipfile.ZipInfo('skills/' + skill + '/' + relative, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = ((0o100755 if mode == '100755' else 0o100644) << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            zipped.writestr(info, git('cat-file', 'blob', oid), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        info = zipfile.ZipInfo('INSTALL.md', (1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        instructions = ('# Install\n\nRequires Python 3.9 or newer. Extract the ZIP, then copy `skills/' + skill + '/` to your agent skills directory (for Codex, `~/.agents/skills/' + skill + '/`). Do not overwrite an existing installation without reviewing it. Start a new session to load the skill.\n')
        zipped.writestr(info, instructions.encode('utf-8'), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        info = zipfile.ZipInfo('LICENSE', (1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        zipped.writestr(info, git('show', tag + ':LICENSE'), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def validate(directory):
    """Install the archives in a private directory and generate a first report."""
    with tempfile.TemporaryDirectory(prefix='arche-release-check-') as tmp:
        root = Path(tmp)
        extracted = root / 'extracted'
        extracted.mkdir()
        install = root / 'installed'
        install.mkdir()
        for name in SKILLS:
            with zipfile.ZipFile(directory / (name + '.zip')) as zipped:
                if 'LICENSE' not in zipped.namelist():
                    raise ValueError('license missing from ' + name)
                zipped.extractall(extracted)
            shutil.copytree(extracted / 'skills' / name, install / name)
        env = {'PATH': os.environ.get('PATH', ''), 'LANG': 'C.UTF-8', 'PYTHONNOUSERSITE': '1', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'XDG_CONFIG_HOME': str(root / 'config')}
        repo = root / 'fixture'
        repo.mkdir()
        (repo / 'main.py').write_text('def main():\n    return "hello"\n', encoding='utf-8')
        out = root / 'report'
        scripts = install / 'arche' / 'scripts'
        for command in ([sys.executable, '-B', str(scripts / 'scan.py'), str(repo), '--out', str(out)],
                        [sys.executable, '-B', str(scripts / 'imports.py'), '--out', str(out)],
                        [sys.executable, '-B', str(scripts / 'render.py'), '--out', str(out)]):
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        if not (out / 'report.html').is_file():
            raise ValueError('first report missing')
        evaluation = install / 'arche-eval'
        if not (evaluation / 'fixtures' / 'pickup' / 'index.html').is_file():
            raise ValueError('evaluation pickup fixture missing')
        subprocess.run([sys.executable, '-B', str(evaluation / 'scripts' / 'evaluate.py'), 'list'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        studies = evaluation / 'scripts' / 'studies'
        subprocess.run([sys.executable, '-B', str(studies / 'run.py'), '--cases', 'dispatch', '--dry-run', '--output', str(root / 'study')], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        runs = root / 'runs.jsonl'
        runs.write_text(json.dumps({'case_id': 'dispatch', 'steps': [], 'edges': []}) + '\n')
        subprocess.run([sys.executable, '-B', str(studies / 'harness_score.py'), str(studies / 'golden.json'), str(runs)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tag', help='existing tag vMAJOR.MINOR.PATCH[-prerelease]')
    parser.add_argument('--output', required=True, type=Path, help='new output directory')
    args = parser.parse_args()
    if not TAG.fullmatch(args.tag):
        parser.error('tag must be a version such as v1.2.3 or v1.2.3-rc.1')
    try:
        git('rev-parse', '--verify', 'refs/tags/' + args.tag + '^{commit}')
        out = args.output.resolve()
        out.mkdir(parents=True, exist_ok=False)
        for skill in SKILLS:
            archive('refs/tags/' + args.tag, skill, out / (skill + '.zip'))
        validate(out)
        sums = ''.join(hashlib.sha256((out / (name + '.zip')).read_bytes()).hexdigest() + '  ' + name + '.zip\n' for name in SKILLS)
        (out / 'SHA256SUMS').write_text(sums, encoding='ascii')
        print('Validated archives in ' + str(out))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, 'release packaging failed: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
