#!/usr/bin/env python3
"""Run repository tests in a private HOME and PTY-safe shell without user config.

All Git remotes must use file:// or local paths. No test writes to user dotfiles.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(path):
    try:
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(65536), b''):
                digest.update(chunk)
        return digest.hexdigest()
    except FileNotFoundError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('/tmp/bettergit-validation'))
    parser.add_argument('--pattern', default='test*.py')
    parser.add_argument('--measure', action='store_true', help='also measure real plan/apply subprocess overhead')
    ns = parser.parse_args()
    ns.output.mkdir(parents=True, exist_ok=True)
    git = shutil.which('git')
    zsh = shutil.which('zsh')
    if not git or not zsh:
        parser.error('git and zsh are required')
    real_home = Path.home()
    protected = [real_home / name for name in ('.zshrc', '.zshenv', '.zprofile', '.zlogin', '.gitconfig', '.config/git/config')]
    before = {str(p): fingerprint(p) for p in protected}
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'python': sys.version.split()[0],
              'git': str(Path(git).resolve()), 'isolation': {'private_home': True, 'private_zdotdir': True,
              'system_git_config_disabled': True, 'network_protocols_disabled': True}, 'checks': []}
    with tempfile.TemporaryDirectory(prefix='bettergit-isolated-') as temporary:
        base = Path(temporary)
        for name in ('home', 'xdg', 'tmp', 'zdot', 'templates', 'shell'):
            (base / name).mkdir()
        config = base / 'test.gitconfig'
        config.write_text('[init]\n defaultBranch = main\n')
        path = os.pathsep.join(dict.fromkeys([str(Path(sys.executable).parent), str(Path(git).parent),
                                             '/usr/bin', '/bin', '/usr/sbin', '/sbin']))
        env = {'PATH': path, 'HOME': str(base / 'home'), 'XDG_CONFIG_HOME': str(base / 'xdg'),
               'ZDOTDIR': str(base / 'zdot'), 'TMPDIR': str(base / 'tmp'),
               'GIT_CONFIG_GLOBAL': str(config), 'GIT_CONFIG_SYSTEM': os.devnull,
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_ATTR_NOSYSTEM': '1', 'GIT_TEMPLATE_DIR': str(base / 'templates'),
               'GIT_ALLOW_PROTOCOL': 'file', 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never',
               'GIT_DEFAULT_HASH': 'sha1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
               'LC_ALL': 'C', 'LANG': 'C', 'LANGUAGE': 'en', 'PYTHONUTF8': '1', 'TERM': 'xterm-256color'}
        def run(name, command, cwd=ROOT, timeout=120):
            started = time.monotonic()
            destination = ns.output / (name + '.log')
            print(f'[isolated] {name}', flush=True)
            with destination.open('wb') as log:
                process = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            report['checks'].append({'name': name, 'exit_code': process.returncode,
                                     'seconds': round(time.monotonic() - started, 3), 'log': str(destination.resolve())})
            print(destination.read_text(errors='replace')[-3500:], flush=True)
            return process.returncode
        run('versions', [zsh, '-dfc', 'command git --version; command python3 --version; print -r -- $ZSH_VERSION'])
        run('unittest', [sys.executable, '-s', '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', ns.pattern, '-v'])
        if ns.measure:
            run('workflow_measurement', [sys.executable, '-s', '-B', str(ROOT / 'scripts/measure_workflow.py')])
            run('diagnostic_evaluation', [sys.executable, '-s', '-B', str(ROOT / 'scripts/evaluate_diagnostics.py')])
        run('zsh_syntax', [zsh, '-dfn', str(ROOT / 'bettergit.zsh'), str(ROOT / 'smart-edit-min.zsh')])
        smoke = '''
setopt ERR_EXIT
command git init --quiet
before_enter=$(bindkey '^M')
source "$1"
[[ "$(whence -w git)" == 'git: function' ]]
[[ "$(bindkey '^M')" == "$before_enter" ]]
git --version
bettergit doctor --samples 1 --budget 4 --json
[[ ! -e "$HOME/.zshrc" ]]
[[ ! -e "$HOME/.gitconfig" ]]
export BETTERGIT_ENABLED=0
git status --porcelain
print 'isolated zsh: PASS; Enter binding unchanged; no user dotfiles created'
'''
        run('zsh_smoke', [zsh, '-dfc', smoke, 'bettergit-test', str(ROOT / 'bettergit.zsh')], cwd=base / 'shell')
        report['isolated_config_unchanged'] = config.read_text() == '[init]\n defaultBranch = main\n'
    report['protected_user_configs_unchanged'] = all(fingerprint(p) == before[str(p)] for p in protected)
    report['passed'] = (all(check['exit_code'] == 0 for check in report['checks'])
                        and report['protected_user_configs_unchanged'] and report['isolated_config_unchanged'])
    (ns.output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
