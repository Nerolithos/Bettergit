import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_bettergit as fixtures
import bettergit as bg
import bettergit_diagnostics as diag
from bettergit_process import capture


class DiagnosticsTests(unittest.TestCase):
    def test_sampling_caps_depth_entries_and_skips_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            (root / 'node_modules').mkdir()
            (root / 'node_modules/secret').touch()
            (root / 'child/.git').mkdir(parents=True)
            (root / 'cycle').symlink_to(root, target_is_directory=True)
            sample = diag.topology_sample(root)
            self.assertEqual(sample['symlinks_skipped'], 1)
            self.assertIn('node_modules', sample['generated_directory_candidates'])
            self.assertEqual(sample['nested_git_markers'], [{'path': 'child/.git', 'marker': 'directory'}])
            capped = diag.topology_sample(root, limit=2)
            self.assertEqual(capped['entries_sampled'], 2)
            self.assertTrue(capped['truncated'])

    def test_trace_handles_sessions_malformed_events_and_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / 'trace'
            events = [
                {'event': 'child_start', 'sid': 'one', 'child_id': 0, 'argv': ['ssh', 'token-secret']},
                {'event': 'child_exit', 'sid': 'two', 'child_id': 0},
                {'event': 'region_enter', 'sid': 'one', 'thread': 'main', 'category': 'index', 'label': 'refresh'},
                {'event': 'region_leave', 'sid': 'one', 'category': 'status', 't_rel': .12},
                {'event': 'region_leave', 'sid': 'one', 'category': 'token-secret', 't_rel': 1},
                {'event': 'child_start', 'sid': 'two', 'child_id': 1, 'argv': 3},
            ]
            trace.write_text('\n'.join(json.dumps(e) for e in events) + '\n[]\n{invalid\n')
            report = diag.trace_report(trace)
            self.assertIn('transport', [f['code'] for f in report['findings']])
            self.assertIn('filesystem', [f['code'] for f in report['findings']])
            self.assertNotIn('token-secret', json.dumps(report))
            self.assertEqual(report['completed_spans_seconds']['status'], .12)

    def test_trace_truncation_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / 'trace'
            trace.write_text('x' * 300000 + '\n')
            self.assertTrue(diag.trace_report(trace)['truncated'])

    def assert_child_stopped(self, exit_parent):
        with tempfile.TemporaryDirectory() as tmp:
            heartbeat = Path(tmp) / 'heartbeat'
            child = 'import sys,time\nwhile True:\n open(sys.argv[1],"a").write("x")\n time.sleep(.02)'
            parent = ('import subprocess,sys,time,pathlib; '
                      'p=subprocess.Popen([sys.executable,"-c",sys.argv[2],sys.argv[1]]); '
                      '\nwhile not pathlib.Path(sys.argv[1]).exists(): time.sleep(.01)\n' +
                      ('' if exit_parent else 'time.sleep(30)'))
            start = time.monotonic()
            rc, _, _ = capture([sys.executable, '-c', parent, str(heartbeat), child], timeout=.4 if not exit_parent else 2)
            self.assertEqual(rc, 0 if exit_parent else 124)
            self.assertLess(time.monotonic() - start, 3)
            self.assertTrue(heartbeat.exists(), 'descendant must have actually started')
            before = heartbeat.read_bytes()
            time.sleep(.15)
            self.assertEqual(heartbeat.read_bytes(), before, 'orphan helper is still running')

    def test_process_timeout_kills_descendants(self):
        self.assert_child_stopped(exit_parent=False)

    def test_descendant_killed_even_if_probe_parent_exits(self):
        self.assert_child_stopped(exit_parent=True)

    def test_probe_output_limit_fails_closed(self):
        rc, out, _ = capture([sys.executable, '-c', 'print("x" * 1000)'], timeout=1, limit=32)
        self.assertEqual(rc, 125)
        self.assertEqual(len(out), 32)

    def test_global_budget_stops_slow_probes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'git'
            fake.write_text('#!/bin/sh\nsleep 30\n')
            fake.chmod(0o755)
            start = time.monotonic()
            report = diag.collect(git=str(fake), seconds=.15, samples=2)
            self.assertLess(time.monotonic() - start, 2)
            self.assertIn('budget_exhausted', [m['status'] for m in report['measurements']])

    def test_invalid_budgets(self):
        for value in (-1, 0, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                diag.collect(seconds=value)


class RepositoryDiagnosticsTests(unittest.TestCase):
    setUp = fixtures.RepositoryTests.setUp
    git = fixtures.RepositoryTests.git
    configure = fixtures.RepositoryTests.configure
    commit = fixtures.RepositoryTests.commit
    remote_change = fixtures.RepositoryTests.remote_change

    def test_default_diagnosis_never_contacts_remote(self):
        self.git('remote', 'set-url', 'origin', 'https://secret@example.invalid/repo')
        calls = []
        def spy(command, **kwargs):
            calls.append(command)
            return capture(command, **kwargs)
        with patch.object(diag, 'capture', side_effect=spy):
            report = diag.collect(seconds=5, samples=2)
        self.assertFalse(any('ls-remote' in command for command in calls))
        order = [m['name'] for m in report['measurements'] if m['name'].startswith('status_')]
        self.assertEqual(order, ['status_no', 'status_normal', 'status_normal', 'status_no'])
        self.assertNotIn('secret', json.dumps(report))
        self.assertIn('topology', report)

    def test_explicit_network_error_classification_redacts_url(self):
        self.git('remote', 'set-url', 'origin', 'https://secret-token@example.invalid/repo')
        calls = []
        def fake_network(command, **kwargs):
            calls.append(command)
            if 'ls-remote' in command:
                return 128, '', 'fatal: could not resolve host https://secret-token@example.invalid/repo'
            return capture(command, **kwargs)
        with patch.object(diag, 'capture', side_effect=fake_network):
            report = diag.collect(seconds=6, samples=1, network=True, remote='origin')
        self.assertTrue(any('ls-remote' in command for command in calls))
        self.assertIn('dns_failure', [f['code'] for f in report['findings']])
        self.assertNotIn('secret-token', json.dumps(report))

    def test_local_remote_is_not_network_probed(self):
        report = diag.collect(seconds=5, samples=1, network=True, remote='origin')
        self.assertIn('local_remote', [f['code'] for f in report['findings']])
        self.assertFalse(any(m['name'] == 'remote_read' for m in report['measurements']))

    def test_baseline_save_compare_and_json_stdout(self):
        cli = str(Path(bg.__file__).resolve())
        process = subprocess.run([sys.executable, cli, 'doctor', '--samples', '1', '--budget', '6', '--save-baseline', '--json'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(json.loads(process.stdout)['baseline_saved'])
        path = Path('.git/bettergit/diagnostic-baseline.json')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        process = subprocess.run([sys.executable, cli, 'doctor', '--samples', '1', '--budget', '6', '--compare', '--json'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertIn('status_normal', report['baseline_comparison'])


if __name__ == '__main__':
    unittest.main()
