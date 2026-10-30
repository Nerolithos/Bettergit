import contextlib
import io
import json
import os
import pty
import select
import time
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_bettergit as fixtures
import bettergit as bg
from bettergit_workflow import Store


# Reuse the real remote/clone fixture without re-running its inherited test methods.
class WorkflowTests(unittest.TestCase):
    setUp = fixtures.RepositoryTests.setUp
    git = fixtures.RepositoryTests.git
    configure = fixtures.RepositoryTests.configure
    commit = fixtures.RepositoryTests.commit
    remote_change = fixtures.RepositoryTests.remote_change

    def prepare(self):
        workflow = bg.engine()
        with workflow.store.lock():
            return workflow, workflow.prepare()

    def test_plan_apply_and_recovery_preserve_uncommitted_work(self):
        self.remote_change()
        workflow, operation = self.prepare()
        head = self.git('rev-parse', 'HEAD')
        self.assertEqual(operation['before'], head)
        self.assertEqual(operation['state'], 'planned')
        self.assertEqual(workflow.store.load(operation['id'])['target'], operation['target'])
        self.assertFalse(Path('remote.txt').exists())
        with workflow.store.lock(), patch.object(bg, 'confirm', return_value=True):
            # Engine captures callbacks at construction.
            workflow.confirm = bg.confirm
            self.assertEqual(workflow.apply(operation), 0)
        self.assertEqual(workflow.store.load(operation['id'])['state'], 'succeeded')
        Path('file.txt').write_text('unstaged')
        Path('staged.txt').write_text('staged')
        self.git('add', 'staged.txt')
        Path('untracked.txt').write_text('untracked')
        index = Path('.git/index').read_bytes()
        after = self.git('rev-parse', 'HEAD')
        before_files = {p.name: p.read_bytes() for p in Path('.').iterdir() if p.is_file()}
        with patch.object(bg, 'confirm', return_value=True):
            workflow.confirm = bg.confirm
            summary = workflow.recover(operation['id'], preview=False)
        self.assertTrue(summary['created'])
        self.assertEqual(self.git('rev-parse', 'refs/heads/' + summary['recovery_branch']), head)
        self.assertEqual(self.git('rev-parse', 'HEAD'), after)
        self.assertEqual(Path('.git/index').read_bytes(), index)
        self.assertEqual({p.name: p.read_bytes() for p in Path('.').iterdir() if p.is_file()}, before_files)

    def test_upstream_drift_invalidates_saved_plan(self):
        self.remote_change()
        workflow, operation = self.prepare()
        self.remote_change('second.txt', 'new')
        self.git('fetch')
        workflow.confirm = lambda _: self.fail('must not ask after drift')
        with self.assertRaisesRegex(RuntimeError, 'upstream has changed'):
            workflow.apply(operation)
        self.assertFalse(Path('remote.txt').exists())

    def test_config_drift_invalidates_saved_plan(self):
        self.remote_change()
        workflow, operation = self.prepare()
        self.git('config', 'pull.rebase', 'true')
        with self.assertRaisesRegex(RuntimeError, 'configuration has changed'):
            workflow.apply(operation)

    def test_same_commit_different_branch_invalidates_plan(self):
        self.remote_change()
        workflow, operation = self.prepare()
        self.git('checkout', '-b', 'new-branch')
        with self.assertRaisesRegex(RuntimeError, 'branch/HEAD'):
            workflow.apply(operation)

    def test_repository_lock_fails_closed_and_releases(self):
        workflow = bg.engine()
        with workflow.store.lock():
            with self.assertRaisesRegex(RuntimeError, 'Another bettergit'):
                with Store(workflow.common).lock():
                    self.fail('lock should be exclusive')
        with Store(workflow.common).lock():
            pass

    def test_default_push_remote_differs_from_upstream(self):
        fork = self.root / 'fork.git'
        self.git('init', '--bare', str(fork))
        self.git('remote', 'add', 'fork', str(fork))
        self.git('config', 'remote.pushDefault', 'fork')
        self.git('config', 'push.default', 'current')
        branch = self.git('symbolic-ref', '--short', 'HEAD')
        self.git('push', 'fork', 'HEAD')
        context = bg.engine().context()
        self.assertEqual(context['remote'], 'origin')
        self.assertEqual(context['push_target'], {'remote': 'fork', 'ref': 'refs/heads/' + branch})

    def test_plan_refuses_dirty_before_fetch(self):
        Path('untracked').touch()
        workflow = bg.engine()
        workflow.run_git = lambda _: self.fail('must not fetch dirty workspace')
        with self.assertRaisesRegex(RuntimeError, 'not clean'):
            workflow.prepare()

    def test_linked_worktree_cannot_apply_other_worktree_plan(self):
        self.remote_change()
        workflow, operation = self.prepare()
        linked = self.root / 'linked'
        self.git('worktree', 'add', '-b', 'linked', str(linked))
        original = Path.cwd()
        os.chdir(linked)
        try:
            second = bg.engine()
            self.assertEqual(second.common, workflow.common)
            self.assertNotEqual(second.gitdir, workflow.gitdir)
            self.assertEqual(second.store.load(operation['id'])['before'], operation['before'])
            with self.assertRaisesRegex(RuntimeError, 'another worktree'):
                second.apply(operation)
        finally:
            os.chdir(original)

    def test_hook_failure_recorded_and_can_abort(self):
        Path('local.txt').write_text('local')
        self.commit(self.local)
        self.remote_change()
        hook = Path('.git/hooks/pre-merge-commit')
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        workflow, operation = self.prepare()
        workflow.confirm = lambda _: True
        self.assertNotEqual(workflow.apply(operation), 0)
        self.assertEqual(workflow.store.load(operation['id'])['state'], 'failed')
        self.assertTrue(Path('.git/MERGE_HEAD').exists())
        self.assertEqual(workflow.resume(operation['id'], abort=True), 0)
        self.assertEqual(workflow.store.load(operation['id'])['state'], 'aborted')
        self.assertEqual(self.git('rev-parse', 'HEAD'), operation['before'])
        self.assertFalse(Path('remote.txt').exists())

    def test_hook_failure_can_continue_after_fix(self):
        Path('local.txt').write_text('local')
        self.commit(self.local)
        self.remote_change()
        hook = Path('.git/hooks/pre-merge-commit')
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        workflow, operation = self.prepare()
        workflow.confirm = lambda _: True
        self.assertNotEqual(workflow.apply(operation), 0)
        hook.unlink()
        with patch.dict(os.environ, GIT_EDITOR='true'):
            self.assertEqual(workflow.resume(operation['id']), 0)
        self.assertEqual(workflow.store.load(operation['id'])['state'], 'succeeded')

    def test_post_merge_hook_changes_are_flagged_for_review(self):
        self.remote_change()
        hook = Path('.git/hooks/post-merge')
        hook.write_text('#!/bin/sh\nprintf "hook change" >> file.txt\n')
        hook.chmod(0o755)
        workflow, operation = self.prepare()
        workflow.confirm = lambda _: True
        self.assertEqual(workflow.apply(operation), 1)
        saved = workflow.store.load(operation['id'])
        self.assertEqual(saved['state'], 'needs_review')
        self.assertEqual(saved['exit_code'], 0)
        self.assertFalse(saved['post_state_clean'])
        self.assertIn('hook change', Path('file.txt').read_text())

    def test_interrupted_state_durable(self):
        self.remote_change()
        workflow, operation = self.prepare()
        workflow.confirm = lambda _: True
        def interrupt(_):
            raise KeyboardInterrupt
        workflow.run_git = interrupt
        with self.assertRaises(KeyboardInterrupt):
            workflow.apply(operation)
        self.assertEqual(workflow.store.load(operation['id'])['state'], 'interrupted')
        self.assertEqual(self.git('rev-parse', operation['before_ref']), operation['before'])

    def test_refuses_to_abort_unrelated_operation(self):
        self.remote_change()
        workflow, operation = self.prepare()
        operation['state'] = 'failed'
        workflow.store.save(operation)
        with self.assertRaisesRegex(RuntimeError, 'matching'):
            workflow.resume(operation['id'], abort=True)

    def test_modified_backup_ref_refused(self):
        self.remote_change()
        workflow, operation = self.prepare()
        self.git('update-ref', operation['before_ref'], operation['target'])
        workflow.confirm = lambda _: True
        with self.assertRaisesRegex(RuntimeError, '(?i)recovery reference'):
            workflow.apply(operation)
        with self.assertRaisesRegex(RuntimeError, '(?i)recovery reference'):
            workflow.recover(operation['id'])

    def test_conflict_with_newline_filename(self):
        name = 'odd\nfile.txt'
        Path(name).write_text('base')
        self.commit(self.local)
        self.git('push')
        self.git('pull', '--ff-only', cwd=self.other)
        Path(name).write_text('local')
        self.commit(self.local)
        self.remote_change(name, 'remote')
        workflow, operation = self.prepare()
        self.assertEqual(operation['state'], 'conflicted')
        self.assertIn(name, operation['conflicts'])
        self.assertEqual(Path(name).read_text(), 'local')

    def test_preview_failure_does_not_write_merge_state(self):
        Path('local.txt').write_text('local')
        self.commit(self.local)
        self.remote_change()
        workflow = bg.engine()
        original = workflow.probe
        def fail_preview(args, **kwargs):
            return (124, '', '') if args[:1] == ['merge-tree'] else original(args, **kwargs)
        workflow.probe = fail_preview
        with self.assertRaisesRegex(RuntimeError, 'preview'):
            workflow.prepare()
        self.assertFalse(Path('.git/MERGE_HEAD').exists())
        self.assertFalse(Path('remote.txt').exists())

    def test_recovery_branch_collision_does_not_overwrite(self):
        self.remote_change()
        workflow, operation = self.prepare()
        branch = 'bettergit/recover/' + operation['id']
        self.git('branch', branch, operation['target'])
        workflow.confirm = lambda _: True
        with self.assertRaises(RuntimeError):
            workflow.recover(operation['id'], preview=False)
        self.assertEqual(self.git('rev-parse', branch), operation['target'])

    def test_json_plan_cli_and_noninteractive_apply(self):
        self.remote_change()
        cli = str(Path(bg.__file__).resolve())
        process = subprocess.run([sys.executable, cli, 'sync', '--plan', '--json'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        plan = json.loads(process.stdout)
        self.assertEqual(plan['state'], 'planned')
        process = subprocess.run([sys.executable, cli, 'sync', '--apply', plan['id']], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertFalse(Path('remote.txt').exists())
        result = subprocess.check_output([sys.executable, cli, 'history', '--json'], text=True)
        self.assertEqual(json.loads(result)[0]['state'], 'planned')

    def test_real_terminal_confirmation_applies_saved_plan(self):
        self.remote_change()
        workflow, operation = self.prepare()
        cli = str(Path(bg.__file__).resolve())
        master, slave = pty.openpty()
        process = subprocess.Popen([sys.executable, cli, 'sync', '--apply', operation['id']],
                                   stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        transcript = b''
        deadline = time.monotonic() + 15
        answered = False
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], .1)
                if ready:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    transcript += chunk
                    if b'[y/N]' in transcript and not answered:
                        os.write(master, b'y\n')
                        answered = True
                if process.poll() is not None and not ready:
                    break
            self.assertTrue(answered, transcript.decode(errors='replace'))
            self.assertEqual(process.wait(timeout=3), 0, transcript.decode(errors='replace'))
            self.assertTrue(Path('remote.txt').exists())
            self.assertEqual(workflow.store.load(operation['id'])['state'], 'succeeded')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)

    def test_corrupt_history_is_visible_and_path_traversal_refused(self):
        workflow = bg.engine()
        directory = workflow.store.path / 'operations'
        directory.mkdir(parents=True)
        (directory / ('a' * 32 + '.json')).write_text('{broken')
        self.assertEqual(workflow.store.history()[0]['state'], 'unreadable')
        with self.assertRaisesRegex(RuntimeError, 'Invalid'):
            workflow.store.load('../../config')


if __name__ == '__main__':
    unittest.main()
