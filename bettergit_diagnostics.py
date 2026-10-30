"""Structured, budgeted diagnostics. No network request unless explicitly selected."""
from collections import deque
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
import shlex
import platform
import sys
import time

from bettergit_process import capture


class Budget:
    def __init__(self, seconds):
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('The diagnostic budget must be a finite positive number')
        self.end = time.monotonic() + seconds

    def remaining(self, maximum=3):
        return max(0, min(maximum, self.end - time.monotonic()))


def finding(report, code, evidence, interpretation, next_step, confidence='medium'):
    report['findings'].append(dict(code=code, evidence=evidence, interpretation=interpretation,
                                   next_step=next_step, confidence=confidence))


def remote_failure(rc, error):
    """Return fixed categories only; remote stderr is never part of the report."""
    if rc == 0:
        return 'remote_reachable', 'The selected remote answered this read-only request', 'No connection repair is indicated by this attempt'
    if rc == 124:
        return 'network_timeout', 'The remote probe exceeded its time allowance; the waiting stage is unknown', 'Inspect transport, credential prompts, proxy, and server availability; retry with a larger budget'
    if rc == 125:
        return 'remote_output_limit', 'The remote probe exceeded its output limit', 'Inspect the remote helper locally; the probe did not establish reachability'
    # Specific failures precede broad authentication/TLS patterns.
    categories = [
        ('ssh_host_key_failure', ('host key verification failed', 'remote host identification has changed'), 'SSH could not verify the server identity', 'Verify the server fingerprint through a trusted channel before updating known_hosts'),
        ('proxy_failure', ('could not resolve proxy:', 'proxy connect aborted', 'connect tunnel failed', 'proxy authentication required', 'received http code 407'), 'The configured proxy could not complete the request', 'Inspect the selected proxy and its authentication; do not disable TLS verification'),
        ('dns_failure', ('could not resolve host:', 'could not resolve host ', 'could not resolve hostname ', 'name or service not known', 'nodename nor servname provided'), 'The transport reported a hostname resolution failure', 'Check DNS and the configured remote hostname; compare with another trusted network'),
        ('connection_refused', ('connection refused', 'failed to connect to'), 'The transport could not establish a connection', 'Check the destination port, firewall, proxy, and service availability'),
        ('authentication_failure', ('authentication failed', 'permission denied (publickey', 'could not read username', 'terminal prompts disabled', 'http basic: access denied'), 'The transport reported an authentication failure', 'Check the selected SSH identity or credential helper and repository access'),
        ('remote_repository_unavailable', ('repository not found', 'does not appear to be a git repository'), 'The repository was not found or access was concealed', 'Verify the remote path and account access; this does not prove the repository is absent'),
        ('tls_failure', ('ssl certificate problem:', 'certificate verification failed', 'server certificate verification failed', 'ssl_connect', 'tls handshake'), 'The transport reported a TLS verification or handshake failure', 'Check the certificate chain, clock, and trusted proxy configuration; keep TLS verification enabled'),
    ]
    for code, patterns, meaning, action in categories:
        if any(pattern in error.lower() for pattern in patterns):
            return code, meaning, action
    return 'remote_read_failed', 'The request failed without a recognized transport signature', 'Inspect the native Git error locally; no specific cause was established'


def finalize(report):
    """Separate a completed diagnosis from incomplete observations."""
    incomplete = [m['name'] for m in report.get('measurements', []) if m['status'] != 'ok']
    report['coverage'] = {'complete': not incomplete, 'incomplete_probes': incomplete,
                          'network_requested': report.get('network_requested', False)}
    if incomplete:
        report['findings'] = [f for f in report['findings'] if f['code'] != 'no_clear_bottleneck']
        finding(report, 'incomplete_evidence', 'One or more probes failed, timed out, or were skipped',
                'Missing measurements cannot rule out a bottleneck',
                'Inspect the named incomplete probes; retry with a larger budget if needed', 'unknown')
    substantive = [f for f in report['findings'] if f['confidence'] not in ('unknown', 'low')
                   and f['code'] not in ('remote_reachable', 'local_remote')]
    report['assessment'] = {'state': 'findings_with_gaps' if substantive and incomplete else 'findings' if substantive else 'incomplete' if incomplete else 'inconclusive',
                            'finding_codes': [f['code'] for f in substantive],
                            'meaning': 'Observed evidence is not a guarantee that every cause has been identified'}
    report['limitations'] = report.get('limitations', []) + [
        'Status probes disable fsmonitor and ignore submodules to avoid external helper execution; those costs are not measured',
        'Git optional index writes are disabled during status probes']
    return report


def child_program(event):
    """Inspect executable positions, not URLs or arbitrary argument substrings."""
    argv = event.get('argv', [])
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return '', []
    try:
        if event.get('use_shell') or len(argv) == 1:
            argv = shlex.split(argv[0]) + argv[1:]
        if not argv:
            return '', []
        program = Path(argv[0]).name.lower()
        if program in ('sh', 'bash', 'zsh') and '-c' in argv:
            i = argv.index('-c')
            argv = shlex.split(argv[i + 1])
            program = Path(argv[0]).name.lower() if argv else ''
        return program, argv[1:]
    except (ValueError, IndexError):
        return '', []


def trace_report(path):
    report = {'schema': 1, 'kind': 'trace', 'findings': [], 'truncated': False}
    regions, children = {}, {}
    durations = {}
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - 262144)
            f.seek(start)
            raw = f.read(262144)
        report['truncated'] = start > 0
        for line in raw.splitlines():
            try:
                e = json.loads(line)
                if not isinstance(e, dict):
                    continue
                key = (e.get('sid'), e.get('thread'), e.get('category'), e.get('label'), e.get('nesting'))
                child = (e.get('sid'), e.get('child_id'))
                event = e.get('event')
                if event == 'region_enter':
                    regions[key] = e
                elif event == 'region_leave':
                    regions.pop(key, None)
                    category = str(e.get('category', 'unknown'))
                    elapsed = e.get('t_rel')
                    if isinstance(elapsed, (int, float)) and math.isfinite(elapsed) and elapsed >= 0:
                        # Nested spans overlap, so do not pretend this is a percentage breakdown.
                        durations[category] = max(durations.get(category, 0), elapsed)
                elif event == 'child_start':
                    children[child] = e
                elif event == 'child_exit':
                    children.pop(child, None)
                elif event == 'exit':
                    regions = {k: v for k, v in regions.items() if k[0] != e.get('sid')}
                    children = {k: v for k, v in children.items() if k[0] != e.get('sid')}
            except (ValueError, TypeError, UnicodeError):
                continue
        filesystem = any(e.get('category') in ('index', 'dir', 'read_directory') or
                         e.get('label') in ('read_directory', 'untracked', 'refresh') for e in regions.values())
        programs = [child_program(e) for e in children.values()]
        classes = {e.get('child_class') for e in children.values() if isinstance(e.get('child_class'), str)}
        transport = (any(c.startswith('transport') for c in classes) or
                     any(p in ('ssh', 'plink', 'git-remote-http', 'git-remote-https', 'git-upload-pack', 'git-receive-pack')
                         or p == 'git' and any(a in ('fetch', 'push', 'upload-pack', 'receive-pack', 'remote-http', 'remote-https') for a in args[:3])
                         for p, args in programs))
        authentication = any(p.startswith(('git-credential-', 'git-askpass', 'ssh-askpass')) or
                             p == 'git' and any(a.startswith('credential-') for a in args[:2]) for p, args in programs)
        interaction = ('editor' in classes or any(p in ('vim', 'nvim', 'vi', 'nano', 'emacs', 'code', 'subl', 'gpg', 'gpg2', 'pinentry', 'pinentry-mac') for p, _ in programs))
        if filesystem:
            finding(report, 'filesystem', 'A file scan or index operation is still active',
                    'Directory traversal, file metadata reads, or storage latency may be involved', 'Run doctor to compare scan modes and sample the directory layout')
        if transport:
            finding(report, 'transport', 'A remote transport subprocess is still running',
                    'Network, proxy, authentication, or server response may cause this wait; this does not prove a network outage',
                    'Run doctor --network for an optional, time-limited read-only remote probe')
        if authentication:
            finding(report, 'authentication', 'A credential helper is still running', 'Authentication or a credential service may be waiting', 'Check the credential prompt and helper configuration')
        if 'hook' in classes:
            finding(report, 'hook', 'A Git hook is still running', 'A build, test, or hook task may still be running', 'Inspect the hook before deciding how to proceed')
        if interaction:
            finding(report, 'interaction', 'An editor or signing helper is active', 'The command may be waiting for user interaction', 'Check the editor or signing prompt')
        # Only emit allowlisted category names; arbitrary Trace2 labels may carry paths or secrets.
        report['completed_spans_seconds'] = {k: round(v, 6) for k, v in durations.items()
                                             if k in {'index', 'status', 'dir', 'unpack_trees', 'read_directory', 'checkout', 'merge'}}
    except OSError:
        report['trace_unavailable'] = True
    if not report['findings']:
        finding(report, 'unknown', 'There is not enough active-stage evidence', 'The cause is currently unknown', 'Run doctor to collect comparison measurements', 'unknown')
    return report


def topology_sample(root, limit=3000, max_depth=4, seconds=1.5):
    """Runs only in a killable helper: even a blocked scandir cannot hang the CLI."""
    deadline = time.monotonic() + seconds
    queue = deque([(Path(root), 0)])
    count = dirs = links = 0
    nested, candidates = [], []
    cut = False
    heavy = {'node_modules', '.venv', 'venv', 'target', 'dist', 'build', 'vendor', '.cache'}
    while queue and count < limit and time.monotonic() < deadline:
        directory, depth = queue.popleft()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if count >= limit or time.monotonic() >= deadline:
                        cut = True
                        break
                    count += 1
                    rel = str(Path(entry.path).relative_to(root))
                    if entry.name == '.git':
                        if depth:
                            nested.append({'path': rel, 'marker': 'directory' if entry.is_dir(follow_symlinks=False) else 'file'})
                        continue
                    if entry.is_symlink():
                        links += 1
                        continue
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    dirs += 1
                    if entry.name in heavy:
                        candidates.append(rel)
                        continue
                    if depth < max_depth:
                        queue.append((Path(entry.path), depth + 1))
                    else:
                        cut = True
        except OSError:
            cut = True
    return {'entries_sampled': count, 'directories_sampled': dirs, 'symlinks_skipped': links,
            'nested_git_markers': nested, 'generated_directory_candidates': candidates,
            'truncated': cut or bool(queue), 'max_depth': max_depth, 'entry_limit': limit}


def collect(git='git', cwd=None, seconds=10, samples=2, network=False, remote=None):
    budget = Budget(seconds)
    started = time.monotonic()
    report = {'schema': 1, 'kind': 'doctor', 'created_at': datetime.now(timezone.utc).isoformat(),
              'budget_seconds': seconds, 'measurements': [], 'findings': [], 'network_requested': network,
              'environment': {'platform': platform.system(), 'python': platform.python_version()},
              'sampling': {'repetitions': samples, 'order': 'alternating', 'cache_controlled': False,
                           'probe_policy': 'no-fsmonitor-no-submodules-no-optional-locks-v1'}}
    reserve = min(4, seconds * .4) if network else 0
    report['schedule'] = {'remote_reserved_seconds': reserve}

    def measure(name, command, cap=2):
        allowance = budget.remaining(cap)
        if name.startswith('status_') or name == 'topology':
            allowance = min(allowance, max(0, budget.remaining(seconds) - reserve))
        before = time.monotonic()
        if allowance <= .01:
            report['measurements'].append({'name': name, 'status': 'budget_exhausted'})
            return 124, '', ''
        if command[0] == git and '--no-optional-locks' not in command:
            command = [git, '--no-optional-locks', '-c', 'core.fsmonitor=false'] + command[1:]
        rc, out, err = capture(command, cwd=cwd, timeout=allowance)
        report['measurements'].append({'name': name, 'seconds': round(time.monotonic() - before, 6),
                                       'status': 'ok' if rc == 0 else 'timeout' if rc == 124 else 'output_limit' if rc == 125 else 'failed', 'exit_code': rc})
        return rc, out, err

    # Separate launch cost from repository work. No extra --version process per sample.
    for _ in range(samples):
        rc, version, _ = measure('process_start', [git, '--version'], cap=1)
        if rc == 0 and version.startswith('git version '):
            report['environment']['git_version'] = version.strip()[:100]
    rc, root, _ = measure('repository', [git, 'rev-parse', '--show-toplevel'])
    if rc:
        finding(report, 'repository_unavailable', 'Repository discovery failed or exceeded its time budget', 'Repository diagnostics cannot continue', 'Run this command inside a non-bare Git worktree', 'high')
        report['elapsed_seconds'] = round(time.monotonic() - started, 6)
        return finalize(report)
    root = root.removesuffix('\n')
    rc, out, _ = measure('git_directory', [git, 'rev-parse', '--absolute-git-dir'], cap=1)
    if rc == 0 and (Path(out.removesuffix('\n')) / 'index.lock').exists():
        finding(report, 'index_lock', 'index.lock exists', 'Another Git operation may own the lock', 'Identify the owning process before changing the lock file')
    # Warm both modes; alternate order to reduce ordering bias, retain every observation.
    for i in range(samples):
        for mode in (('no', 'normal') if i % 2 == 0 else ('normal', 'no')):
            measure('status_' + mode, [git, '--no-optional-locks', '-c', 'core.fsmonitor=false', 'status', '--ignore-submodules=all', '--porcelain=v1', '-z', '--untracked-files=' + mode])
    timings = {}
    for name in ('process_start', 'status_no', 'status_normal'):
        values = [m['seconds'] for m in report['measurements'] if m['name'] == name and m['status'] == 'ok']
        if values:
            timings[name] = round(statistics.median(values), 6)
    report['median_seconds'] = timings
    if timings.get('process_start', 0) > .15:
        finding(report, 'process_start_cost', f"Median Git startup time:  {timings['process_start']:.3f}s",
                'Process startup or system inspection adds overhead; this is not a network measurement', 'Reduce sequential Git launches and inspect system load or endpoint security overhead')
    if 'status_no' in timings and 'status_normal' in timings and timings['status_normal'] > max(.2, timings['status_no'] * 3):
        finding(report, 'untracked_scan_cost', 'Enabling untracked-file scanning increased elapsed time substantially',
                'The comparison suggests untracked-directory traversal may be expensive', 'Inspect ignore rules and test untrackedCache or fsmonitor in a controlled experiment')
    allowance = min(budget.remaining(2), max(0, budget.remaining(seconds) - reserve))
    if allowance > .1:
        rc, out, _ = measure('topology', [sys.executable, str(Path(__file__).resolve()), '_topology', root], cap=2)
        if rc == 0:
            try:
                report['topology'] = json.loads(out)
                if report['topology']['generated_directory_candidates']:
                    finding(report, 'generated_directories', 'The sample contains common dependency or build directories',
                            'These directories may already be ignored and may not be the bottleneck', 'Check .gitignore; the sampler does not descend into these directories', 'low')
            except (ValueError, KeyError):
                report['topology'] = {'unavailable': True}
                report['measurements'].append({'name': 'topology_decode', 'status': 'failed'})
    else:
        report['measurements'].append({'name': 'topology', 'status': 'budget_exhausted'})
    if network:
        if remote is None:
            # Resolve only the active branch rather than choosing an arbitrary configured remote.
            rc, branch, _ = measure('current_branch', [git, 'symbolic-ref', '--quiet', '--short', 'HEAD'], cap=1)
            if rc == 0:
                rc, out, _ = measure('active_remote', [git, 'config', '--get', 'branch.' + branch.removesuffix('\n') + '.remote'], cap=1)
                if rc == 0:
                    remote = out.removesuffix('\n')
        if remote:
            rc, out, _ = measure('remote_url', [git, 'remote', 'get-url', '--', remote], cap=1)
            if rc == 0:
                # Do not persist or display the URL, userinfo, paths, or raw remote errors.
                url = out.removesuffix('\n')
                network_remote = '://' in url and not url.startswith('file://') or ':' in url and not url.startswith(('/', './', '../'))
                if network_remote:
                    start = time.monotonic()
                    allowance = budget.remaining(4)
                    if allowance > .01:
                        rc, _, err = capture([git, '-c', 'credential.interactive=false', 'ls-remote', '--quiet', '--', remote, 'HEAD'],
                                              cwd=cwd, timeout=allowance,
                                              env={'GIT_ASKPASS': 'false', 'SSH_ASKPASS': 'false'})
                        report['measurements'].append({'name': 'remote_read', 'seconds': round(time.monotonic() - start, 6),
                                                       'status': 'ok' if rc == 0 else 'timeout' if rc == 124 else 'output_limit' if rc == 125 else 'failed', 'exit_code': rc})
                        code, meaning, action = remote_failure(rc, err)
                        finding(report, code, f'Read-only Git remote probe exit code: {rc}',
                                meaning, action, 'high' if rc == 0 else 'medium')
                    else:
                        report['measurements'].append({'name': 'remote_read', 'status': 'budget_exhausted'})
                else:
                    finding(report, 'local_remote', 'The remote uses a local path or file transport', 'No network probe is needed', 'Inspect local storage and file permissions', 'high')
            else:
                finding(report, 'remote_unavailable', 'The remote configuration is unavailable', 'No network probe was started', 'Pass the name of a configured remote with --remote', 'high')
        else:
            finding(report, 'remote_unavailable', 'The current branch has no identifiable remote', 'No network probe was started', 'Select a configured remote with --remote', 'high')
    if not report['findings']:
        finding(report, 'no_clear_bottleneck', 'This comparison did not identify a clear bottleneck', 'Caching or intermittent waits may affect the result', 'Collect Trace2 evidence while the problem is occurring', 'unknown')
    report['elapsed_seconds'] = round(time.monotonic() - started, 6)
    report['limitations'] = ['Disk caching and system load affect measurements', 'Directory sampling is bounded and does not follow symbolic links',
                            'Remote error signatures are heuristic; the probe uses Git proxy, SSH, and authentication settings and describes only this attempt' if network else 'No network probe was requested']
    return finalize(report)


def compare_baseline(report, baseline):
    """Refuse to turn incomparable/corrupt measurements into speedup claims."""
    if not isinstance(baseline, dict) or not isinstance(baseline.get('median_seconds'), dict):
        raise ValueError('The baseline format is invalid; save a new baseline.')
    previous_environment = baseline.get('environment')
    previous_sampling = baseline.get('sampling')
    if previous_environment != report.get('environment') or previous_sampling != report.get('sampling'):
        report['baseline_incompatible'] = True
        report['baseline_comparison'] = {}
        return
    comparison = {}
    for name, value in report.get('median_seconds', {}).items():
        old = baseline['median_seconds'].get(name)
        if old is not None:
            if isinstance(old, bool) or not isinstance(old, (int, float)) or not math.isfinite(old) or old <= 0:
                raise ValueError('The baseline contains invalid durations; save a new baseline.')
            comparison[name] = {'previous_seconds': old, 'current_seconds': value, 'ratio': round(value / old, 3)}
    report['baseline_comparison'] = comparison
    report['baseline_created_at'] = baseline.get('created_at')


def render(report, say):
    if 'assessment' in report:
        say('Assessment: ' + report['assessment']['state'])
        missing = report['coverage']['incomplete_probes']
        if missing:
            say('Incomplete probes: ' + ', '.join(missing))
    for m in report.get('measurements', []):
        say(f"{m['name']}: {m.get('seconds', 0):.3f}s ({m['status']})")
    for f in report['findings']:
        say(f"[{f['code']}; confidence={f['confidence']}] Evidence: {f['evidence']}; Interpretation: {f['interpretation']}; Next: {f['next_step']}")
    topology = report.get('topology', {})
    if 'entries_sampled' in topology:
        say(f"Directory sample: {topology['entries_sampled']} entries; skipped {topology['symlinks_skipped']} symbolic links; truncated={topology['truncated']}")
        for path in topology['generated_directory_candidates']:
            say('Dependency/build directory candidate: ' + repr(path))
        for item in topology['nested_git_markers']:
            say('Nested Git marker: ' + repr(item['path']) + ' (' + item['marker'] + ')')


if __name__ == '__main__' and sys.argv[1:2] == ['_topology']:
    print(json.dumps(topology_sample(sys.argv[2]), ensure_ascii=True))
