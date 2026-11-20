"""Exercise release packaging using an isolated, local Git repository."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def test_tagged_skill_archives_are_reproducible_and_installable(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    for name in ('arche', 'arche-eval'):
        shutil.copytree(ROOT / 'skills' / name, repo / 'skills' / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(ROOT / 'tools', repo / 'tools', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy(ROOT / 'LICENSE', repo / 'LICENSE')
    env = {'PATH': os.environ['PATH'], 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'XDG_CONFIG_HOME': str(tmp_path / 'config'),
           'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.test',
           'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.test'}
    def run(*command):
        return subprocess.run(command, cwd=repo, env=env, check=True, capture_output=True, text=True)
    run('git', 'init', '-q')
    run('git', 'add', '.')
    run('git', 'commit', '-qm', 'synthetic release')
    run('git', 'tag', 'v1.2.3-rc.1')
    first, second = tmp_path / 'first', tmp_path / 'second'
    run(sys.executable, 'tools/package-release.py', 'v1.2.3-rc.1', '--output', str(first))
    (repo / 'skills/arche/SKILL.md').write_text('dirty working tree')
    run(sys.executable, 'tools/package-release.py', 'v1.2.3-rc.1', '--output', str(second))
    for name in ('arche', 'arche-eval'):
        one, two = first / (name + '.zip'), second / (name + '.zip')
        assert one.read_bytes() == two.read_bytes()
        with zipfile.ZipFile(one) as archive:
            assert 'INSTALL.md' in archive.namelist()
            assert archive.read('LICENSE') == (ROOT / 'LICENSE').read_bytes()
            assert 'skills/' + name + '/SKILL.md' in archive.namelist()
            assert all(not part.startswith('.') for path in archive.namelist() for part in path.split('/'))
    assert (first / 'SHA256SUMS').read_bytes() == (second / 'SHA256SUMS').read_bytes()
    assert subprocess.run([sys.executable, 'tools/package-release.py', 'v1.2.3-rc.1', '--output', str(first)], cwd=repo, env=env, capture_output=True).returncode != 0
