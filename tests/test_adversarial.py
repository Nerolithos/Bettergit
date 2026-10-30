"""Behavioral regressions from the isolated product-depth review."""
import os
from pathlib import Path
import unittest

import test_bettergit as fixtures
import bettergit as bg


class AdversarialTests(unittest.TestCase):
    setUp = fixtures.RepositoryTests.setUp
    git = fixtures.RepositoryTests.git
    configure = fixtures.RepositoryTests.configure
    commit = fixtures.RepositoryTests.commit
    remote_change = fixtures.RepositoryTests.remote_change

    def test_dirty_submodule_cannot_be_hidden_by_ignore_all(self):
        self.git('-c', 'protocol.file.allow=always', 'submodule', 'add', str(self.other), 'module')
        self.commit(self.local)
        self.git('config', 'submodule.module.ignore', 'all')
        Path('module/file.txt').write_text('uncommitted submodule data')
        self.assertEqual(self.git('status', '--porcelain'), '', 'fixture must reproduce hidden modifications')
        with self.assertRaisesRegex(RuntimeError, 'not clean'):
            bg.engine().require_clean()

    def test_preview_side_effect_must_block_execution(self):
        Path('local.txt').write_text('local')
        self.commit(self.local)
        self.remote_change()
        workflow = bg.engine()
        with workflow.store.lock():
            operation = workflow.prepare()
        original = workflow.probe
        previews = 0
        def side_effect(args, **kwargs):
            nonlocal previews
            result = original(args, **kwargs)
            if args[:1] == ['merge-tree']:
                previews += 1
                # The final, post-confirmation preview changes an unrelated file.
                if previews == 1:
                    Path('file.txt').write_text('changed by a merge driver')
            return result
        workflow.probe = side_effect
        workflow.confirm = lambda _: True
        original_run = workflow.run_git
        def refuse_merge(args):
            if args[:1] == ['merge']:
                self.fail('dirty preview side effects must be caught before invoking merge')
            return original_run(args)
        workflow.run_git = refuse_merge
        with self.assertRaisesRegex(RuntimeError, 'not clean'):
            workflow.apply(operation)
        self.assertEqual(self.git('rev-parse', 'HEAD'), operation['before'])

    def test_ignored_file_collision_preserves_existing_content(self):
        Path('.gitignore').write_text('remote.txt\n')
        self.commit(self.local)
        self.remote_change()
        Path('remote.txt').write_text('private ignored data')
        workflow = bg.engine()
        head = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(RuntimeError, 'existing local content'):
            workflow.prepare()
        self.assertEqual(Path('remote.txt').read_text(), 'private ignored data')
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
    def test_ignored_file_created_during_confirmation_is_preserved(self):
        Path('.gitignore').write_text('remote.txt\n')
        self.commit(self.local)
        self.remote_change()
        workflow = bg.engine()
        operation = workflow.prepare()
        def confirm(_):
            Path('remote.txt').write_text('created while waiting for confirmation')
            return True
        workflow.confirm = confirm
        with self.assertRaisesRegex(RuntimeError, 'existing local content'):
            workflow.apply(operation)
        self.assertEqual(Path('remote.txt').read_text(), 'created while waiting for confirmation')
        self.assertEqual(self.git('rev-parse', 'HEAD'), operation['before'])



if __name__ == '__main__':
    unittest.main()
