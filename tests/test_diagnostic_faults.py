import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import test_bettergit as fixtures
import bettergit_diagnostics as diag
from bettergit_process import capture
from diagnostic_cases import CASES


class DiagnosticClassifierTests(unittest.TestCase):
    def test_labelled_matrix_and_negative_controls(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace'
            for name, events, expected in CASES:
                with self.subTest(case=name):
                    path.write_text('\n'.join(json.dumps(e) for e in events))
                    actual = {f['code'] for f in diag.trace_report(path)['findings']} - {'unknown'}
                    self.assertEqual(actual, expected)

    def test_stream_limit_stops_producer_before_it_completes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            completed = Path(tmp) / 'completed'
            producer = 'import os,sys,time,pathlib\nwhile True: os.write(1,b"x"*65536)\npathlib.Path(sys.argv[1]).touch()'
            started = time.monotonic()
            rc, out, _ = capture([sys.executable, '-c', producer, str(completed)], timeout=3, limit=4096)
            self.assertEqual(rc, 125)
            self.assertEqual(len(out), 4096)
            self.assertLess(time.monotonic() - started, 2)
            self.assertFalse(completed.exists())

    def test_explicit_remote_probe_gets_reserved_time_after_slow_status(self):
        clock = [0.0]
        def fake_capture(command, **kwargs):
            is_status = 'status' in command
            duration = min(kwargs['timeout'], 2 if is_status else .1)
            clock[0] += duration
            rc = 124 if is_status else 0
            output = ('git version 2.45.2\n' if '--version' in command else
                      'https://example.invalid/repo\n' if 'get-url' in command else '/tmp\n')
            return rc, output, ''
        with patch.object(diag.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(diag, 'capture', side_effect=fake_capture):
            report = diag.collect(seconds=3, samples=1, network=True, remote='origin')
        self.assertIn('remote_reachable', {f['code'] for f in report['findings']})
        self.assertTrue(any(m['name'] == 'status_no' and m['status'] == 'timeout' for m in report['measurements']))
        self.assertLessEqual(clock[0], 3)

    def test_baseline_refuses_environment_mismatch_and_corrupt_numbers(self):
        report = {'environment': {'git_version': '2'}, 'sampling': {'repetitions': 2}, 'median_seconds': {'status_no': 1}}
        baseline = {'environment': {'git_version': '1'}, 'sampling': {'repetitions': 2}, 'median_seconds': {'status_no': 2}}
        diag.compare_baseline(report, baseline)
        self.assertTrue(report['baseline_incompatible'])
        self.assertEqual(report['baseline_comparison'], {})
        baseline['environment'] = report['environment']
        baseline['median_seconds']['status_no'] = float('nan')
        with self.assertRaises(ValueError):
            diag.compare_baseline(report, baseline)



class ControlledGitFaultTests(unittest.TestCase):
    setUp = fixtures.RepositoryTests.setUp
    git = fixtures.RepositoryTests.git
    configure = fixtures.RepositoryTests.configure
    commit = fixtures.RepositoryTests.commit
    remote_change = fixtures.RepositoryTests.remote_change

    def observe_wait(self, kind):
        ready = self.root / (kind + '.ready')
        trace = self.root / (kind + '.trace')
        helper = self.root / {'hook': 'waiting-hook', 'interaction': 'code', 'transport': 'ssh', 'authentication': 'git-credential-test'}[kind]
        helper.write_text('#!' + sys.executable + '\nimport pathlib,time\npathlib.Path(' + repr(str(ready)) + ').touch()\ntime.sleep(30)\n')
        helper.chmod(0o755)
        command = ['git']
        data = None
        env = dict(os.environ, GIT_TRACE2_EVENT=str(trace), GIT_TERMINAL_PROMPT='0')
        if kind == 'hook':
            hook = Path('.git/hooks/pre-commit')
            hook.write_text('#!/bin/sh\nexec ' + str(helper) + '\n')
            hook.chmod(0o755)
            command += ['commit', '--allow-empty', '-m', 'fault']
        elif kind == 'interaction':
            env['GIT_EDITOR'] = str(helper)
            command += ['commit', '--allow-empty']
        elif kind == 'transport':
            # Git invokes our fake SSH executable, which never creates a socket.
            env.update(GIT_ALLOW_PROTOCOL='ssh', GIT_SSH_COMMAND=str(helper), GIT_SSH_VARIANT='ssh')
            command += ['ls-remote', 'ssh://host.invalid/nope']
        else:
            command += ['-c', 'credential.helper=', '-c', 'credential.helper=!' + str(helper), 'credential', 'fill']
            data = b'protocol=https\nhost=example.invalid\n\n'
        with (self.root / (kind + '.log')).open('wb') as log:
            p = subprocess.Popen(command, env=env, stdin=subprocess.PIPE if data else subprocess.DEVNULL,
                                 stdout=log, stderr=log, start_new_session=True)
            try:
                if data:
                    p.stdin.write(data)
                    p.stdin.close()
                deadline = time.monotonic() + 8
                while not ready.exists() and p.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(ready.exists(), (self.root / (kind + '.log')).read_text())
                report = diag.trace_report(trace)
                self.assertIn(kind, {f['code'] for f in report['findings']}, report)
                self.assertNotIn(str(self.root), json.dumps(report))
            finally:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                p.wait()

    def test_actual_slow_git_hook(self):
        self.observe_wait('hook')

    def test_actual_editor_wait(self):
        self.observe_wait('interaction')

    def test_actual_git_transport_with_fake_ssh(self):
        self.observe_wait('transport')

    def test_actual_credential_helper_wait(self):
        self.observe_wait('authentication')

    def test_injected_process_start_latency_is_measured(self):
        helper = self.root / 'slow-git'
        real_git = shutil.which('git')
        helper.write_text('#!' + sys.executable + '\nimport os,sys,time\ntime.sleep(.18)\nos.execv(' + repr(real_git) + ',[' + repr(real_git) + ']+sys.argv[1:])\n')
        helper.chmod(0o755)
        report = diag.collect(git=str(helper), seconds=5, samples=1)
        self.assertIn('process_start_cost', {f['code'] for f in report['findings']})
        self.assertGreater(report['median_seconds']['process_start'], .15)


if __name__ == '__main__':
    unittest.main()
