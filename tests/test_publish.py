import json
from pathlib import Path
import unittest
from unittest.mock import patch
import bettergit as bg
import test_bettergit as fixtures


class PublishTests(unittest.TestCase):
    setUp = fixtures.RepositoryTests.setUp
    git = fixtures.RepositoryTests.git
    configure = fixtures.RepositoryTests.configure
    commit = fixtures.RepositoryTests.commit
    remote_change = fixtures.RepositoryTests.remote_change

    def local_commit(self):
        Path('publish.txt').write_text('publish this commit')
        self.commit(Path.cwd())
        return self.git('rev-parse', 'HEAD')

    def test_push_local_commit_and_verify_remote(self):
        head = self.local_commit()
        self.assertEqual(bg.sync(push=True, yes=True), 0)
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/heads/main').split()[0], head)
        records = bg.engine().store.history()
        self.assertEqual(records[0]['publication']['state'], 'verified')

    def test_push_is_opt_in(self):
        head = self.local_commit()
        old = self.git('rev-parse', 'origin/main')
        self.assertEqual(bg.sync(), 0)
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/heads/main').split()[0], old)
        self.assertNotEqual(head, old)

    def test_dirty_worktree_stops_without_automatic_commit(self):
        old = self.git('rev-parse', 'HEAD')
        Path('pending.txt').write_text('not committed')
        self.assertEqual(bg.sync(push=True, yes=True), 1)
        self.assertEqual(self.git('rev-parse', 'HEAD'), old)
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/heads/main').split()[0], old)

    def test_declined_push_preserves_local_commit(self):
        head = self.local_commit()
        with patch.object(bg, 'confirm', return_value=False):
            self.assertEqual(bg.sync(push=True), 1)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertNotEqual(self.git('ls-remote', 'origin', 'refs/heads/main').split()[0], head)

    def test_divergence_merges_then_publishes(self):
        self.local_commit()
        self.remote_change()
        with patch.object(bg, 'confirm', return_value=True):
            self.assertEqual(bg.sync(push=True), 0)
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/heads/main').split()[0], self.git('rev-parse', 'HEAD'))
        self.assertEqual(len(self.git('rev-list', '--parents', '-n', '1', 'HEAD').split()), 3)

    def test_different_push_destination_is_rejected(self):
        self.local_commit()
        self.git('remote', 'set-url', '--push', 'origin', str(self.root / 'other.git'))
        self.assertEqual(bg.sync(push=True, yes=True), 1)

    def test_remote_race_rejects_without_force(self):
        self.local_commit()
        old = self.git('rev-parse', 'origin/main')
        def concurrent_change(_):
            self.remote_change()
            return True
        with patch.object(bg, 'confirm', side_effect=concurrent_change):
            self.assertNotEqual(bg.sync(push=True), 0)
        self.assertNotEqual(self.git('ls-remote', 'origin', 'refs/heads/main').split()[0], old)
        self.assertNotEqual(self.git('ls-remote', 'origin', 'refs/heads/main').split()[0], self.git('rev-parse', 'HEAD'))

    def test_configuration_change_after_confirmation_stops(self):
        self.local_commit()
        def change(_):
            self.git('config', 'push.followTags', 'true')
            return True
        with patch.object(bg, 'confirm', side_effect=change):
            self.assertEqual(bg.sync(push=True), 1)

    def test_hook_branch_change_cannot_change_published_commit(self):
        head = self.local_commit()
        old = self.git('rev-parse', 'HEAD^')
        hook = Path('.git/hooks/pre-push')
        hook.write_text('#!/bin/sh\ngit update-ref refs/heads/main ' + old + '\n')
        hook.chmod(0o755)
        self.assertEqual(bg.sync(push=True, yes=True), 1)
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/heads/main').split()[0], head)

    def test_invalid_cli_combinations_are_rejected(self):
        for args in (['sync', '--yes'], ['sync', '--push', '--plan']):
            with self.subTest(args=args), self.assertRaises(SystemExit) as caught:
                bg.main(args)
            self.assertEqual(caught.exception.code, 2)

    def test_remote_verification_failure_is_recorded_as_unverified(self):
        self.local_commit()
        original = bg.probe
        def unavailable(args, **kwargs):
            if args[0] == 'ls-remote':
                return 124, '', 'timeout'
            return original(args, **kwargs)
        with patch.object(bg, 'probe', side_effect=unavailable):
            self.assertEqual(bg.sync(push=True, yes=True), 1)
        self.assertEqual(bg.engine().store.history()[0]['publication']['state'], 'unverified')

    def test_declined_merge_never_pushes(self):
        self.local_commit()
        self.remote_change()
        remote = self.git('ls-remote', 'origin', 'refs/heads/main').split()[0]
        with patch.object(bg, 'confirm', return_value=False):
            self.assertNotEqual(bg.sync(push=True, yes=True), 0)
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/heads/main').split()[0], remote)

    def test_stale_up_to_date_plan_cannot_publish_new_commit(self):
        self.local_commit()
        workflow = bg.engine()
        with workflow.store.lock():
            operation = workflow.prepare()
        Path('another.txt').write_text('later')
        self.commit(Path.cwd())
        self.assertEqual(bg.sync(operation_id=operation['id'], push=True, yes=True), 1)
