#!/usr/bin/env python3
"""Evidence-based Git assistance. Python 3.10+, no third-party dependencies."""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading

GIT = os.environ.get('BETTERGIT_GIT', 'git')


def say(message):
    print('[bettergit] ' + message, file=sys.stderr, flush=True)


def probe(args, cwd=None, timeout=3, input_data=None):
    from bettergit_process import capture
    return capture([GIT, *args], cwd=cwd, timeout=timeout, input_data=input_data)


def diagnose(trace=None, cwd=None, **options):
    from bettergit_diagnostics import collect, render, trace_report
    report = trace_report(trace) if trace else collect(git=GIT, cwd=cwd, **options)
    render(report, say)
    return report


def threshold():
    try:
        value = float(os.environ.get('BETTERGIT_SLOW_SECONDS', '5'))
        if not math.isfinite(value) or value <= 0:
            raise ValueError
        return value
    except ValueError:
        raise ValueError('BETTERGIT_SLOW_SECONDS must be a finite positive number')


def run_git(args):
    seconds = threshold()
    with tempfile.TemporaryDirectory(prefix='bettergit-') as tmp:
        trace = str(Path(tmp) / 'trace.json')
        env = dict(os.environ)
        # Respect user-owned tracing; diagnostics use a separate trace only if unset.
        own_trace = not env.get('GIT_TRACE2_EVENT')
        if own_trace:
            env['GIT_TRACE2_EVENT'] = trace
        stop = threading.Event()
        def watch():
            if not stop.wait(seconds):
                say(f'Git has been running for over {seconds:g}s; inspecting evidence while the command continues.')
                try:
                    if own_trace:
                        diagnose(trace)
                    else:
                        say('Preserving the existing GIT_TRACE2_EVENT setting; run doctor for separate diagnostics.')
                except OSError:
                    say('Diagnostics are unavailable; the original command continues.')
                say('Large operations or interaction may explain the delay; use Ctrl-C to interrupt.')
        p = subprocess.Popen([GIT, *args], env=env)
        worker = threading.Thread(target=watch, daemon=True)
        worker.start()
        try:
            rc = p.wait()
        except KeyboardInterrupt:
            # Foreground terminal delivers SIGINT to both wrapper and Git.
            p.send_signal(signal.SIGINT)
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.terminate()
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
            rc = 130
        finally:
            stop.set()
            worker.join(timeout=7)
        return rc if rc >= 0 else 128 - rc


def confirm(question):
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        say('Non-interactive session: confirmation-required actions are skipped.')
        return False
    print('[bettergit] ' + question + ' [y/N] ', file=sys.stderr, end='', flush=True)
    return sys.stdin.readline().strip().lower() in ('y', 'yes')


def engine():
    from bettergit_workflow import Engine
    return Engine(probe, run_git, confirm, say)


def sync(fetch=True, plan_only=False, operation_id=None, json_output=False, push=False, yes=False):
    try:
        workflow = engine()
        with workflow.store.lock():
            if operation_id:
                operation = workflow.store.load(operation_id)
            else:
                operation = workflow.prepare(fetch=fetch, persist=fetch)
            if json_output:
                print(json.dumps(operation, ensure_ascii=True, indent=2))
            else:
                workflow.describe(operation)
            if operation['mergeability'] == 'conflicted':
                return 1
            if not fetch or plan_only:
                return 0
            if operation['mergeability'] != 'up_to_date':
                rc = workflow.apply(operation)
                if rc:
                    return rc
            if push:
                from bettergit_publish import publish
                return publish(workflow, operation, yes=yes)
            return 0
    except RuntimeError as exc:
        say(str(exc))
        return 1


TEMPLATES = {
    'base': '.DS_Store\n.env\n.env.*\n!.env.example\n',
    'python': '__pycache__/\n*.py[cod]\n.venv/\n.pytest_cache/\n',
    'node': 'node_modules/\ndist/\ncoverage/\n',
    'rust': 'target/\n',
    'go': '/bin/\n',
}


def init_ignore(args):
    # Locate target correctly even with init options/--separate-git-dir.
    # Conservative supported interception: plain init or init <directory> only.
    root = Path(args[1]) if len(args) == 2 else Path.cwd()
    if not root.is_dir() or (root / '.gitignore').exists() or not sys.stdin.isatty():
        return
    choices = ['base']
    for marker, template in [('pyproject.toml', 'python'), ('package.json', 'node'), ('Cargo.toml', 'rust'), ('go.mod', 'go')]:
        if (root / marker).exists():
            choices.append(template)
    content = ''.join(TEMPLATES[name] for name in choices)
    say('Suggested .gitignore templates: ' + ', '.join(choices) + '\n' + content)
    if confirm('Create this .gitignore?'):
        try:
            with (root / '.gitignore').open('x') as f:
                f.write(content)
        except FileExistsError:
            say('.gitignore already exists; keeping the original file.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Bettergit: Git diagnostics, durable merge plans, and recovery')
    commands = parser.add_subparsers(dest='action', required=True)
    pr = commands.add_parser('pr', help='Inspect GitHub pull requests without changing them')
    pr_commands = pr.add_subparsers(dest='pr_action', required=True)
    pr_explain = pr_commands.add_parser('explain', help='Explain merge blockers for a captured PR revision')
    pr_explain.add_argument('pull_request', nargs='?', help='PR number or plain HTTPS PR URL')
    pr_explain.add_argument('--repo', help='OWNER/REPO (required with a numeric PR)')
    pr_explain.add_argument('--hostname', help='GitHub or GitHub Enterprise hostname')
    pr_explain.add_argument('--from-json', metavar='FILE', help='Inspect a schema-1 snapshot without network requests')
    pr_explain.add_argument('--budget', type=float, default=20)
    pr_explain.add_argument('--json', action='store_true')
    run = commands.add_parser('run', help='Run Git unchanged and inspect slow commands')
    run.add_argument('args', nargs=argparse.REMAINDER)
    doctor = commands.add_parser('doctor', help='Collect evidence within a time budget')
    doctor.add_argument('--budget', type=float, default=10)
    doctor.add_argument('--samples', type=int, choices=range(1, 6), default=None)
    doctor.add_argument('--experiment', choices=['untracked-cache'], help='Compare temporary-index settings without changing persistent configuration')
    doctor.add_argument('--network', action='store_true', help='Enable an optional read-only remote probe')
    doctor.add_argument('--remote', help='Configured remote name (requires --network)')
    doctor.add_argument('--json', action='store_true')
    doctor.add_argument('--save-baseline', action='store_true')
    doctor.add_argument('--compare', action='store_true')
    sync_parser = commands.add_parser('sync', help='Fetch, preview, and confirm before merging')
    modes = sync_parser.add_mutually_exclusive_group()
    modes.add_argument('--plan', action='store_true', help='Save a plan and recovery references without merging')
    modes.add_argument('--apply', metavar='ID', help='Revalidate and apply a saved plan')
    modes.add_argument('--continue', dest='resume', metavar='ID', help='Continue a recorded unfinished merge')
    modes.add_argument('--abort', metavar='ID', help='Abort a recorded unfinished merge')
    sync_parser.add_argument('--push', action='store_true', help='After synchronization, confirm and push committed history to the matching tracking branch')
    sync_parser.add_argument('--yes', action='store_true', help='Skip push confirmation with --push; merge confirmation is still required')
    sync_parser.add_argument('--json', action='store_true', help='Available with --plan only')
    commands.add_parser('check', help='Inspect the cached upstream without fetching or merging')
    reconcile = commands.add_parser('reconcile', help='Compare records, references, and worktree state without changing them')
    reconcile.add_argument('id')
    reconcile.add_argument('--json', action='store_true')
    history = commands.add_parser('history', help='List saved operation records')
    history.add_argument('--json', action='store_true')
    recover = commands.add_parser('recover', help='Preview a recovery point or create a recovery branch')
    recover.add_argument('id')
    mode = recover.add_mutually_exclusive_group()
    mode.add_argument('--preview', action='store_true')
    mode.add_argument('--create-branch', action='store_true')
    recover.add_argument('--json', action='store_true')
    ns = parser.parse_args(argv)
    try:
        if ns.action == 'pr':
            from bettergit_pr import Client, target, explain, load_snapshot, render
            if ns.from_json:
                if ns.pull_request or ns.repo or ns.hostname:
                    parser.error('--from-json cannot be combined with a PR selector, --repo, or --hostname')
                snapshot = load_snapshot(ns.from_json)
            else:
                if not ns.pull_request:
                    parser.error('pr explain requires a PR selector or --from-json')
                snapshot = Client(target(ns.pull_request, ns.repo, ns.hostname), seconds=ns.budget).collect()
            result = explain(snapshot)
            if ns.json:
                print(json.dumps(result, ensure_ascii=True, indent=2))
            else:
                render(result, say)
            return 0
        if ns.action == 'doctor':
            if ns.experiment:
                if ns.network or ns.remote or ns.compare or ns.save_baseline:
                    parser.error('--experiment cannot be combined with network or baseline options')
                from bettergit_experiments import experiment, render
                report = experiment(git=GIT, seconds=ns.budget, samples=ns.samples or 3)
                if ns.json:
                    print(json.dumps(report, ensure_ascii=True, indent=2))
                else:
                    render(report, say)
                return 0 if report['state'] == 'completed' else 1
            ns.samples = ns.samples or 2
            if ns.remote and not ns.network:
                parser.error('--remote requires --network')
            from bettergit_diagnostics import collect, render, compare_baseline
            from bettergit_workflow import atomic_json
            report = collect(git=GIT, seconds=ns.budget, samples=ns.samples, network=ns.network, remote=ns.remote)
            if ns.compare or ns.save_baseline:
                workflow = engine()
                with workflow.store.lock():
                    path = workflow.store.path / 'diagnostic-baseline.json'
                    if ns.compare:
                        if path.exists():
                            baseline = json.loads(path.read_text())
                            compare_baseline(report, baseline)
                        else:
                            report['baseline_missing'] = True
                    if ns.save_baseline:
                        if not report.get('median_seconds') or any(m['status'] != 'ok' for m in report['measurements'] if m['name'].startswith(('status_', 'process_start'))):
                            raise RuntimeError('Measurements are incomplete; the baseline was not overwritten.')
                        atomic_json(path, {'schema': 1, 'created_at': report['created_at'],
                                          'median_seconds': report['median_seconds'], 'samples': ns.samples,
                                          'environment': report['environment'], 'sampling': report['sampling']})
                        report['baseline_saved'] = True
            if ns.json:
                print(json.dumps(report, ensure_ascii=True, indent=2))
            else:
                render(report, say)
                for name, comparison in report.get('baseline_comparison', {}).items():
                    say(f"Baseline comparison: {name}: {comparison['ratio']:.2f}x (affected by caching and load)")
                if report.get('baseline_missing'):
                    say('No baseline exists; use --save-baseline.')
                if report.get('baseline_incompatible'):
                    say('Baseline version, platform, or sampling differs; skipping ratios. Save a new baseline.')
            return 1 if any(f['code'] == 'repository_unavailable' for f in report['findings']) else 0
        if ns.action == 'sync':
            if ns.push and (ns.plan or ns.resume or ns.abort):
                parser.error('--push cannot be combined with --plan, --continue, or --abort')
            if ns.yes and not ns.push:
                parser.error('--yes requires --push')
            if ns.json and not ns.plan:
                parser.error('--json currently requires sync --plan')
            if ns.resume or ns.abort:
                workflow = engine()
                with workflow.store.lock():
                    return workflow.resume(ns.resume or ns.abort, abort=bool(ns.abort))
            return sync(plan_only=ns.plan, operation_id=ns.apply, json_output=ns.json, push=ns.push, yes=ns.yes)
        if ns.action == 'check':
            return sync(fetch=False)
        if ns.action == 'reconcile':
            workflow = engine()
            with workflow.store.lock():
                result = workflow.reconcile(ns.id)
            if ns.json:
                print(json.dumps(result, ensure_ascii=True, indent=2))
            else:
                say(json.dumps(result, ensure_ascii=True, indent=2))
            return 0
        if ns.action == 'history':
            workflow = engine()
            records = workflow.store.history()
            if ns.json:
                print(json.dumps(records, ensure_ascii=True, indent=2))
            else:
                if not records:
                    say('No operation records yet.')
                for record in records:
                    state = record['state']
                    if state == 'running':
                        state = 'running_or_interrupted (run reconcile before continue/abort/recover)'
                    say(f"{record['id']}  {state}  {record.get('branch', '')!r}  {record.get('created_at', '')}")
            return 0
        if ns.action == 'recover':
            workflow = engine()
            with workflow.store.lock():
                summary = workflow.recover(ns.id, preview=not ns.create_branch)
            if ns.json:
                print(json.dumps(summary, ensure_ascii=True, indent=2))
            else:
                say(json.dumps(summary, ensure_ascii=True, indent=2))
            return 0
        args = ns.args[1:] if ns.args[:1] == ['--'] else ns.args
        if args == ['pull']:
            return sync()
        rc = run_git(args)
        if rc and args[:1] in (['push'], ['pull']):
            # The command may target arbitrary refspecs; never diagnose the wrong target as fact.
            say('Git failed; the push target is not assumed to be upstream. Run check to inspect tracking, or sync --plan to prepare a merge.')
        if rc == 0 and args[:1] == ['init'] and (len(args) == 1 or len(args) == 2 and not args[1].startswith('-')):
            init_ignore(args)
        return rc
    except (RuntimeError, ValueError, OSError) as exc:
        say(str(exc))
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
