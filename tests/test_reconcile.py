import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest

import test_bettergit as fixtures
import bettergit as bg


class CrashRecoveryTests(unittest.TestCase):
    def crash_matrix(self, diverged):
        expected = {'plan_intent': 'preparation_incomplete', 'backups_ready': 'prepared_unrecorded',
                    'plan_ready': 'not_applied', 'before_merge': 'not_applied_or_interrupted',
                    'after_merge': 'completed_unrecorded', 'result_saved': 'completed_unrecorded',
                    'after_ref_ready': 'completed_unrecorded', 'final_saved': 'completed'}
        for checkpoint, classification in expected.items():
            with self.subTest(checkpoint=checkpoint, diverged=diverged):
                fixture = fixtures.RepositoryTests()
                fixture.setUp()
                try:
                    if diverged:
                        Path('local.txt').write_text('local')
                        fixture.commit(fixture.local)
                    fixture.remote_change()
                    barrier = fixture.root / 'barrier.json'
                    worker = Path(__file__).with_name('crash_worker.py')
                    with (fixture.root / 'worker.log').open('wb') as log:
                        p = subprocess.Popen([sys.executable, '-s', '-B', str(worker), checkpoint, str(barrier)],
                                             stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                        try:
                            deadline = time.monotonic() + 12
                            while not barrier.exists() and p.poll() is None and time.monotonic() < deadline:
                                time.sleep(.01)
                            self.assertTrue(barrier.exists(), (fixture.root / 'worker.log').read_text())
                            operation_id = json.loads(barrier.read_text())['id']
                            os.kill(p.pid, signal.SIGKILL)
                            self.assertEqual(p.wait(timeout=3), -signal.SIGKILL)
                        finally:
                            if p.poll() is None:
                                os.killpg(p.pid, signal.SIGKILL)
                                p.wait()
                    workflow = bg.engine()
                    record = workflow.store.path / 'operations' / (operation_id + '.json')
                    record_before = record.read_bytes()
                    index_before = Path('.git/index').read_bytes()
                    refs_before = fixture.git('for-each-ref', '--format=%(refname) %(objectname)')
                    with workflow.store.lock():
                        first = workflow.reconcile(operation_id)
                        second = workflow.reconcile(operation_id)
                    self.assertEqual(first['classification'], classification, first)
                    self.assertEqual(first, second, 'read-only reconciliation must be repeatable')
                    self.assertEqual(record.read_bytes(), record_before)
                    self.assertEqual(Path('.git/index').read_bytes(), index_before)
                    self.assertEqual(fixture.git('for-each-ref', '--format=%(refname) %(objectname)'), refs_before)
                finally:
                    fixture.doCleanups()

    def test_real_sigkill_fast_forward_lifecycle(self):
        self.crash_matrix(False)

    def test_real_sigkill_diverged_merge_lifecycle(self):
        self.crash_matrix(True)


class ReconcileTests(unittest.TestCase):
    setUp = fixtures.RepositoryTests.setUp
    git = fixtures.RepositoryTests.git
    configure = fixtures.RepositoryTests.configure
    commit = fixtures.RepositoryTests.commit
    remote_change = fixtures.RepositoryTests.remote_change

    def prepare(self):
        self.remote_change()
        workflow = bg.engine()
        with workflow.store.lock():
            operation = workflow.prepare()
        return workflow, operation

    def test_external_branch_advance_is_not_mistaken_for_completion(self):
        workflow, operation = self.prepare()
        Path('external.txt').write_text('external')
        self.commit(self.local)
        self.assertEqual(workflow.reconcile(operation['id'])['classification'], 'branch_changed')

    def test_missing_backup_stays_unknown_and_is_not_repaired(self):
        workflow, operation = self.prepare()
        self.git('update-ref', '-d', operation['before_ref'])
        self.assertEqual(workflow.reconcile(operation['id'])['classification'], 'backup_missing')
        rc, _, _ = bg.probe(['show-ref', '--verify', operation['before_ref']])
        self.assertNotEqual(rc, 0)

    def test_unfinished_merge_is_identified_after_hook_failure(self):
        Path('local.txt').write_text('local')
        self.commit(self.local)
        workflow, operation = self.prepare()
        hook = Path('.git/hooks/pre-merge-commit')
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        workflow.confirm = lambda _: True
        self.assertNotEqual(workflow.apply(operation), 0)
        result = workflow.reconcile(operation['id'])
        self.assertEqual(result['classification'], 'matching_unfinished_merge')
        self.assertIn('sync --abort ' + operation['id'], result['suggested_actions'])

    def test_cli_reconcile_outputs_json_without_modifying_log(self):
        workflow, operation = self.prepare()
        record = workflow.store.path / 'operations' / (operation['id'] + '.json')
        before = record.read_bytes()
        result = subprocess.check_output([sys.executable, str(Path(bg.__file__).resolve()), 'reconcile', operation['id'], '--json'], text=True)
        self.assertEqual(json.loads(result)['classification'], 'not_applied')
        self.assertEqual(record.read_bytes(), before)

    def test_git_lock_is_reported_without_deletion(self):
        workflow, operation = self.prepare()
        lock = Path('.git/index.lock')
        lock.touch()
        self.assertEqual(workflow.reconcile(operation['id'])['classification'], 'git_busy')
        self.assertTrue(lock.exists())

    def test_config_change_stales_a_plan(self):
        workflow, operation = self.prepare()
        self.git('config', 'pull.rebase', 'true')
        self.assertEqual(workflow.reconcile(operation['id'])['classification'], 'plan_stale')


if __name__ == '__main__':
    unittest.main()
