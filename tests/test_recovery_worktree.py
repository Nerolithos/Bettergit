import json
from pathlib import Path
import unittest
from unittest.mock import patch
import bettergit as bg
from bettergit_recovery import recover_worktree, history_detail, render_detail
import test_bettergit as fixtures


class RecoveryWorktreeTests(unittest.TestCase):
    setUp = fixtures.RepositoryTests.setUp
    git = fixtures.RepositoryTests.git
    configure = fixtures.RepositoryTests.configure
    commit = fixtures.RepositoryTests.commit
    remote_change = fixtures.RepositoryTests.remote_change

    def plan(self):
        self.remote_change()
        engine = bg.engine()
        with engine.store.lock():
            operation = engine.prepare()
        engine.confirm = lambda _: True
        return engine, operation

    def test_recovery_preserves_dirty_current_index_and_files(self):
        engine, operation = self.plan()
        with engine.store.lock():
            self.assertEqual(engine.apply(operation), 0)
        Path('file.txt').write_text('current unstaged work')
        Path('staged.txt').write_text('staged work')
        self.git('add', 'staged.txt')
        Path('untracked.txt').write_text('untracked work')
        before = self.git('rev-parse', 'HEAD')
        index = Path('.git/index').read_bytes()
        files = {p.name: p.read_bytes() for p in Path('.').iterdir() if p.is_file()}
        target = self.root.resolve() / 'recovered'
        with engine.store.lock():
            summary = recover_worktree(engine, operation['id'], str(target), preview=False)
        self.assertEqual(summary['state'], 'ready')
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=target), operation['before'])
        self.assertFalse((target / 'remote.txt').exists())
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual(Path('.git/index').read_bytes(), index)
        self.assertEqual({p.name: p.read_bytes() for p in Path('.').iterdir() if p.is_file()}, files)
        self.assertIn('recovered', self.git('worktree', 'list'))

    def test_existing_destination_is_never_overwritten(self):
        engine, operation = self.plan()
        target = self.root.resolve() / 'existing'
        target.mkdir()
        marker = target / 'important'; marker.write_text('keep')
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            recover_worktree(engine, operation['id'], str(target), preview=False)
        self.assertEqual(marker.read_text(), 'keep')

    def test_nested_destination_and_symlink_are_refused(self):
        engine, operation = self.plan()
        with self.assertRaisesRegex(RuntimeError, 'outside'):
            recover_worktree(engine, operation['id'], str(Path.cwd() / 'nested'), preview=False)
        link = self.root.resolve() / 'link'; link.symlink_to(self.root)
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            recover_worktree(engine, operation['id'], str(link), preview=False)

    def test_cancel_and_preview_create_nothing(self):
        engine, operation = self.plan()
        target = self.root.resolve() / 'recovered'
        self.assertFalse(recover_worktree(engine, operation['id'], str(target))['created'])
        engine.confirm = lambda _: False
        self.assertFalse(recover_worktree(engine, operation['id'], str(target), preview=False)['created'])
        self.assertFalse(target.exists())

    def test_checkpoint_change_during_confirmation_stops(self):
        engine, operation = self.plan()
        target = self.root.resolve() / 'recovered'
        def change(_):
            self.git('update-ref', operation['before_ref'], operation['target'])
            return True
        engine.confirm = change
        with self.assertRaisesRegex(RuntimeError, 'recovery reference'):
            recover_worktree(engine, operation['id'], str(target), preview=False)
        self.assertFalse(target.exists())

    def test_failed_checkout_preserves_destination_and_record(self):
        engine, operation = self.plan()
        target = self.root.resolve() / 'recovered'
        engine.run_git = lambda _: 1
        with self.assertRaisesRegex(RuntimeError, 'preserved'):
            recover_worktree(engine, operation['id'], str(target), preview=False)
        self.assertTrue(target.is_dir())
        self.assertEqual(engine.store.load(operation['id'])['recovery_worktrees'][0]['state'], 'needs_review')

    def test_hook_dirty_recovery_is_not_reported_ready(self):
        engine, operation = self.plan()
        hook = Path('.git/hooks/post-checkout')
        hook.write_text('#!/bin/sh\nprintf edited > hook-output\n')
        hook.chmod(0o755)
        summary = recover_worktree(engine, operation['id'], str(self.root.resolve() / 'recovered'), preview=False)
        self.assertEqual(summary['state'], 'needs_review')

    def test_detail_is_read_only_and_shows_publication_as_historical(self):
        engine, operation = self.plan()
        operation['publication'] = {'state': 'verified'}
        engine.store.save(operation)
        before = self.git('status', '--porcelain')
        detail = history_detail(engine, operation['id'])
        self.assertTrue(detail['inspection']['read_only'])
        lines=[]; render_detail(detail, lines.append)
        self.assertTrue(any('remote not queried' in line for line in lines))
        self.assertEqual(self.git('status', '--porcelain'), before)

    def test_missing_backup_does_not_hide_history_details(self):
        engine, operation = self.plan()
        self.git('update-ref', '-d', operation['before_ref'])
        detail = history_detail(engine, operation['id'])
        self.assertFalse(detail['recovery']['available'])
        self.assertEqual(detail['inspection']['classification'], 'backup_missing')

    def test_other_worktree_can_inspect_and_recover_shared_checkpoint(self):
        engine, operation = self.plan()
        other = self.root.resolve() / 'other-tree'
        self.git('worktree', 'add', '--detach', str(other), operation['before'])
        import os
        previous = Path.cwd()
        try:
            os.chdir(other)
            linked = bg.engine(); linked.confirm = lambda _: True
            detail = history_detail(linked, operation['id'])
            self.assertEqual(detail['inspection']['classification'], 'other_worktree')
            recovered = self.root.resolve() / 'recovered'
            summary = recover_worktree(linked, operation['id'], str(recovered), preview=False)
            self.assertEqual(summary['state'], 'ready')
        finally:
            os.chdir(previous)

    def test_history_tracks_recovery_and_manual_removal_without_cleanup(self):
        engine, operation = self.plan()
        target = self.root.resolve() / 'recovered'
        recover_worktree(engine, operation['id'], str(target), preview=False)
        detail = history_detail(engine, operation['id'])
        self.assertTrue(detail['recovery_worktrees'][0]['registered'])
        self.git('worktree', 'remove', str(target))
        detail = history_detail(engine, operation['id'])
        self.assertFalse(detail['recovery_worktrees'][0]['registered'])
        self.assertEqual(detail['recovery_worktrees'][0]['recorded_state'], 'ready')

    def test_history_cli_json_includes_observed_state(self):
        import contextlib, io
        engine, operation = self.plan()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(bg.main(['history', operation['id'], '--json']), 0)
        detail = json.loads(output.getvalue())
        self.assertEqual(detail['inspection']['classification'], 'not_applied')
        self.assertTrue(detail['recovery']['available'])

    def test_newline_destination_and_locked_registry(self):
        engine, operation = self.plan()
        target = self.root.resolve() / 'recovered\ncheckout'
        recover_worktree(engine, operation['id'], str(target), preview=False)
        self.git('worktree', 'lock', '--reason', 'keep this recovery', str(target))
        detail = history_detail(engine, operation['id'])
        self.assertTrue(detail['recovery_worktrees'][0]['locked'])
        self.assertEqual(detail['recovery_worktrees'][0]['path'], str(target))

    def test_history_of_ahead_only_sync_survives_later_commits(self):
        Path('local.txt').write_text('ahead')
        self.commit(Path.cwd())
        engine = bg.engine()
        with engine.store.lock():
            operation = engine.prepare()
        self.assertEqual(operation['mergeability'], 'up_to_date')
        self.assertGreater(operation['ahead'], 0)
        Path('later.txt').write_text('later commit')
        self.commit(Path.cwd())
        detail = history_detail(engine, operation['id'])
        self.assertEqual(detail['inspection']['classification'], 'branch_changed')
        self.assertTrue(detail['recovery']['available'])
