"""Native Git clients do not obey the bettergit lock; test overlapping operations."""
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import threading
import time
import unittest

import test_bettergit as fixtures
import bettergit as bg


class NativeConcurrencyTests(unittest.TestCase):
    setUp = fixtures.RepositoryTests.setUp
    git = fixtures.RepositoryTests.git
    configure = fixtures.RepositoryTests.configure
    commit = fixtures.RepositoryTests.commit
    remote_change = fixtures.RepositoryTests.remote_change

    def make_diverged_branch(self):
        Path('local.txt').write_text('local')
        self.commit(self.local)
        self.remote_change()

    def test_orphaned_native_merge_is_not_reported_as_completed(self):
        self.make_diverged_branch()
        ready = self.root / 'hook.ready'
        helper = self.root / 'waiting-hook.py'
        helper.write_text('import pathlib,time\npathlib.Path(' + repr(str(ready)) + ').touch()\ntime.sleep(30)\n')
        hook = Path('.git/hooks/pre-merge-commit')
        hook.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, str(helper)]) + '\n')
        hook.chmod(0o755)
        worker = Path(__file__).with_name('crash_worker.py')
        with (self.root / 'native.log').open('wb') as log:
            p = subprocess.Popen([sys.executable, str(worker), 'unused-checkpoint', str(self.root / 'unused')],
                                 stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and p.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(ready.exists(), (self.root / 'native.log').read_text())
                # Kill only the wrapper. The native Git process/hook remains alive.
                os.kill(p.pid, signal.SIGKILL)
                p.wait(timeout=3)
                workflow = bg.engine()
                record = workflow.store.history()[0]
                record_path = workflow.store.path / 'operations' / (record['id'] + '.json')
                before = record_path.read_bytes()
                index = Path('.git/index').read_bytes()
                with workflow.store.lock():
                    report = workflow.reconcile(record['id'])
                self.assertIn(report['classification'], ('git_busy', 'matching_unfinished_merge', 'not_applied_or_interrupted'))
                self.assertEqual(record_path.read_bytes(), before)
                self.assertEqual(Path('.git/index').read_bytes(), index)
            finally:
                # This is the worker's private process group, not any user terminal.
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                p.wait()

    def test_native_upstream_update_during_real_merge_driver_blocks_apply(self):
        Path('.gitattributes').write_text('file.txt merge=wait-test\n')
        self.commit(self.local)
        self.git('push')
        self.git('pull', '--ff-only', cwd=self.other)
        Path('file.txt').write_text('local side\n')
        self.commit(self.local)
        self.remote_change('file.txt', 'remote side\n')
        armed, ready, release = [self.root / name for name in ('armed', 'driver.ready', 'release')]
        helper = self.root / 'driver.py'
        helper.write_text('import pathlib,sys,time\n'
                          'armed,ready,release=map(pathlib.Path,sys.argv[2:])\n'
                          'if armed.exists():\n ready.touch()\n while not release.exists(): time.sleep(.01)\n'
                          'pathlib.Path(sys.argv[1]).write_text("resolved\\n")\n')
        command = shlex.join([sys.executable, str(helper)]) + ' "%A" ' + shlex.join([str(armed), str(ready), str(release)])
        self.git('config', 'merge.wait-test.driver', command)
        workflow = bg.engine()
        operation = workflow.prepare()
        armed.touch()
        errors = []
        def native_update():
            try:
                deadline = time.monotonic() + 8
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                if not ready.exists():
                    raise RuntimeError('merge driver did not reach its barrier')
                # Simulate another client's force-fetch changing this tracking ref.
                self.git('update-ref', operation['upstream'], operation['before'])
            except BaseException as exc:
                errors.append(exc)
            finally:
                release.touch()
        updater = threading.Thread(target=native_update)
        updater.start()
        workflow.confirm = lambda _: True
        try:
            with self.assertRaisesRegex(RuntimeError, 'upstream has changed'):
                workflow.apply(operation)
        finally:
            release.touch()
            updater.join(timeout=10)
        self.assertFalse(updater.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.git('rev-parse', 'HEAD'), operation['before'])
        self.assertEqual(Path('file.txt').read_text(), 'local side\n')
        self.assertFalse(Path('.git/MERGE_HEAD').exists())


if __name__ == '__main__':
    unittest.main()
