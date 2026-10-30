"""Paired Git experiments using disposable indexes, never persistent config."""
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import statistics
import sys
import tempfile
import time

from bettergit_diagnostics import Budget
from bettergit_process import capture

INDEX_LIMIT = 64 * 1024 * 1024


def snapshot_index(source, destination=None, limit=INDEX_LIMIT):
    """Worker-only bounded copy/hash; the caller can kill blocked filesystem I/O."""
    source = Path(source)
    try:
        before = source.stat()
    except FileNotFoundError:
        return {'exists': False, 'digest': None, 'signature': None}
    if before.st_size > limit:
        raise ValueError('Index exceeds the experiment size limit')
    digest, size = hashlib.sha256(), 0
    output = open(destination, 'wb') if destination else None
    try:
        with source.open('rb') as stream:
            for block in iter(lambda: stream.read(65536), b''):
                size += len(block)
                if size > limit:
                    raise ValueError('Index exceeds the experiment size limit')
                digest.update(block)
                if output:
                    output.write(block)
    finally:
        if output:
            output.close()
    after = source.stat()
    signature = lambda s: [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns]
    if signature(before) != signature(after):
        raise ValueError('Source index changed while taking its snapshot')
    return {'exists': True, 'digest': digest.hexdigest(), 'signature': signature(after)}


def experiment(git='git', cwd=None, seconds=15, samples=3):
    if not isinstance(samples, int) or not 1 <= samples <= 5:
        raise ValueError('Samples must be between 1 and 5')
    budget = Budget(seconds)
    started = time.monotonic()
    report = {'schema': 1, 'kind': 'experiment', 'experiment': 'untracked-cache',
              'created_at': datetime.now(timezone.utc).isoformat(), 'state': 'incomplete',
              'budget_seconds': seconds, 'samples_per_arm': samples, 'measurements': [],
              'environment': {'platform': platform.system(), 'python': platform.python_version()},
              'controls': {'warmups_per_arm': 2, 'measured_order': 'alternating',
                           'fsmonitor': False, 'submodules': 'ignored', 'indexes': 'disposable_copies'},
              'persistent_configuration_changed': False, 'original_index_unchanged': None,
              'recommendation': {'kind': 'inconclusive', 'message': 'Not enough validated evidence yet.'},
              'limitations': ['The OS filesystem cache is not controlled.',
                             'Submodule inspection and fsmonitor are disabled in both arms.',
                             'Results concern untrackedCache only, not all Git performance.',
                             'Filesystem support must be checked before enabling untrackedCache permanently.']}

    def probe(args, env=None, maximum=2):
        # Even rev-parse --shared-index-path may read the index and invoke an
        # fsmonitor hook. Disable it from the first discovery probe onward.
        return capture([git, '-c', 'core.fsmonitor=false', *args], cwd=cwd,
                       timeout=budget.remaining(maximum), env=env)

    def abort(state, message):
        report['state'] = state
        report['recommendation'] = {'kind': 'inconclusive', 'message': message}
        report['elapsed_seconds'] = round(time.monotonic() - started, 6)
        return report

    rc, raw_index, _ = probe(['rev-parse', '--path-format=absolute', '--git-path', 'index'])
    if rc:
        return abort('unavailable', 'The index could not be located within the time budget.')
    index = Path(raw_index.removesuffix('\n'))
    lock = Path(str(index) + '.lock')
    if lock.exists():
        return abort('git_busy', 'The source index is locked; retry after the other Git operation finishes.')
    rc, shared, _ = probe(['rev-parse', '--shared-index-path'])
    if rc:
        return abort('unavailable', 'The index layout could not be verified.')
    if shared.strip():
        return abort('unsupported_layout', 'Split indexes are not yet supported by this experiment; the source index was not changed.')
    rc, configuration, _ = probe(['config', '--null', '--list'])
    if rc:
        return abort('unavailable', 'The repository configuration could not be inspected.')
    for entry in configuration.split('\0'):
        key, _, value = entry.partition('\n')
        if key.startswith('filter.') and key.endswith(('.clean', '.process')) and value:
            return abort('unsupported_filters', 'External clean/process filters are configured; this experiment will not invoke them.')
    rc, version, _ = probe(['--version'], maximum=1)
    if rc or not version.startswith('git version '):
        return abort('unavailable', 'The Git version could not be verified within the time budget.')
    report['environment']['git_version'] = version.strip()[:100]

    with tempfile.TemporaryDirectory(prefix='bettergit-experiment-') as temporary:
        directory = Path(temporary)
        (directory / 'hooks').mkdir()
        base = directory / 'source.index'
        worker = str(Path(__file__).resolve())
        def index_snapshot(destination=None):
            rc, out, _ = capture([sys.executable, worker, '_snapshot', str(index), str(destination) if destination else '', str(INDEX_LIMIT)],
                                  timeout=budget.remaining(2))
            if rc:
                raise RuntimeError('The index snapshot failed, changed, exceeded its size limit, or timed out.')
            return json.loads(out)
        try:
            before = index_snapshot(base)
        except (RuntimeError, ValueError, OSError) as exc:
            return abort('incomplete', str(exc))
        environments = {}
        for arm in ('off', 'on'):
            target = directory / (arm + '.index')
            if before['exists']:
                shutil.copyfile(base, target)
            environments[arm] = {'GIT_INDEX_FILE': str(target), 'GIT_OPTIONAL_LOCKS': '1'}
        prefix = ['-c', 'core.fsmonitor=false', '-c', 'core.splitIndex=false',
                  '-c', 'core.hooksPath=' + str(directory / 'hooks')]
        status_signatures = set()
        failed = False
        def run(arm, phase):
            nonlocal failed
            allowance = budget.remaining(2)
            if allowance <= .01:
                report['measurements'].append({'arm': arm, 'phase': phase, 'status': 'budget_exhausted'})
                failed = True
                return
            start = time.monotonic()
            rc, out, _ = capture([git, *prefix, '-c', 'core.untrackedCache=' + ('true' if arm == 'on' else 'false'),
                                   'status', '--porcelain=v1', '-z', '--untracked-files=normal', '--ignore-submodules=all'],
                                  cwd=cwd, timeout=allowance, env=environments[arm])
            report['measurements'].append({'arm': arm, 'phase': phase, 'seconds': round(time.monotonic()-start, 6),
                                           'status': 'ok' if rc == 0 else 'timeout' if rc == 124 else 'failed', 'exit_code': rc})
            if rc:
                failed = True
            else:
                status_signatures.add(hashlib.sha256(out.encode('utf-8', errors='surrogateescape')).hexdigest())
        # Two warmups per arm, then alternate paired order to reduce ordering bias.
        for arm in ('off', 'on'):
            for _ in range(2):
                run(arm, 'warmup')
        for sample in range(samples):
            for arm in (('off', 'on') if sample % 2 == 0 else ('on', 'off')):
                run(arm, 'measured')
        try:
            after = index_snapshot()
        except (RuntimeError, ValueError, OSError) as exc:
            return abort('incomplete', str(exc))
        report['original_index_unchanged'] = before == after
        rc, after_configuration, _ = probe(['config', '--null', '--list'])
        report['configuration_unchanged'] = rc == 0 and configuration == after_configuration
        report['status_outputs_consistent'] = len(status_signatures) == 1
        if not report['original_index_unchanged'] or not report['configuration_unchanged'] or lock.exists():
            return abort('source_changed', 'Source index/configuration changed or became locked during the experiment; discard this comparison.')
        if failed:
            return abort('incomplete', 'At least one measurement or verification failed or exceeded the shared budget.')
        if not report['status_outputs_consistent']:
            return abort('inconsistent_status', 'Status output changed between measurements; discard this comparison.')
        timings = {arm: statistics.median(m['seconds'] for m in report['measurements']
                                         if m['arm'] == arm and m['phase'] == 'measured' and m['status'] == 'ok')
                   for arm in ('off', 'on')}
        report['median_seconds'] = timings
        report['on_over_off_ratio'] = round(timings['on'] / timings['off'], 4) if timings['off'] > 0 else None
        report['state'] = 'completed'
        if samples < 3:
            report['recommendation'] = {'kind': 'inconclusive', 'message': 'Use at least three measured samples per arm before drawing a conclusion.'}
        elif timings['on'] < timings['off'] * .8 and timings['off'] - timings['on'] >= .02:
            report['recommendation'] = {'kind': 'candidate_only', 'message': 'The temporary-index comparison suggests a benefit. Verify filesystem support and repeat before considering a repository-local setting.'}
        else:
            report['recommendation'] = {'kind': 'no_clear_benefit', 'message': 'This run does not show a clear benefit; keep the existing configuration.'}
        report['elapsed_seconds'] = round(time.monotonic() - started, 6)
        return report


def render(report, say):
    say('Untracked-cache experiment: ' + report['state'])
    for arm, seconds in report.get('median_seconds', {}).items():
        say(f'  cache {arm}: median {seconds:.4f}s')
    say(report['recommendation']['message'])
    say('Original index unchanged: ' + str(report['original_index_unchanged']) + '; no persistent configuration was written.')


if __name__ == '__main__' and sys.argv[1:2] == ['_snapshot']:
    try:
        print(json.dumps(snapshot_index(sys.argv[2], sys.argv[3] or None, int(sys.argv[4]))))
    except (ValueError, OSError) as exc:
        print('Index snapshot unavailable.', file=sys.stderr)
        raise SystemExit(1)
