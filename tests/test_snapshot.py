from collections import Counter
from pathlib import Path
import random
import unittest

import test_bettergit as fixtures
import bettergit as bg
from bettergit_snapshot import RepositorySnapshot
from bettergit_workflow import Engine


class SnapshotTests(unittest.TestCase):
    setUp = fixtures.RepositoryTests.setUp
    git = fixtures.RepositoryTests.git
    configure = fixtures.RepositoryTests.configure
    commit = fixtures.RepositoryTests.commit
    remote_change = fixtures.RepositoryTests.remote_change

    def test_both_recovery_refs_are_atomic_on_collision(self):
        self.remote_change()
        workflow = bg.engine()
        captured = {}
        def checkpoint(stage, operation):
            if stage == 'plan_intent':
                captured.update(operation)
                self.git('update-ref', operation['target_ref'], operation['target'])
        workflow.checkpoint = checkpoint
        with self.assertRaisesRegex(RuntimeError, 'transaction failed'):
            workflow.prepare()
        rc, _, _ = bg.probe(['show-ref', '--verify', captured['before_ref']])
        self.assertNotEqual(rc, 0, 'a failed multi-ref transaction must not leave a partial before ref')
        self.assertEqual(self.git('rev-parse', captured['target_ref']), captured['target'])
        self.assertEqual(workflow.store.load(captured['id'])['state'], 'preparing')

    def test_custom_fetch_namespace_and_case_sensitive_branch(self):
        self.git('checkout', '-b', 'FeatureCase')
        self.git('push', '-u', 'origin', 'HEAD')
        self.git('config', 'remote.origin.fetch', '+refs/heads/*:refs/cache/*')
        self.git('fetch')
        snapshot = RepositorySnapshot.read(bg.engine())
        self.assertEqual(snapshot.context()['branch'], 'FeatureCase')
        self.assertEqual(snapshot.context()['upstream'], 'refs/cache/FeatureCase')
        self.assertEqual(snapshot.refs[snapshot.context()['upstream']], snapshot.state.head)

    def test_newline_worktree_root_uses_unambiguous_fallback(self):
        linked = self.root / 'newline\nroot'
        self.git('worktree', 'add', '-b', 'newline-root', str(linked))
        import os
        original = Path.cwd()
        os.chdir(linked)
        try:
            workflow = bg.engine()
            self.assertEqual(Path(workflow.root).resolve(), linked.resolve())
            self.assertIn('\n', workflow.root)
            self.assertNotEqual(Path(workflow.gitdir), Path(workflow.common))
        finally:
            os.chdir(original)

    def test_direct_git_invocation_budget_fast_forward(self):
        self.check_call_budget(False, 25)

    def test_direct_git_invocation_budget_diverged(self):
        self.check_call_budget(True, 30)

    def check_call_budget(self, diverged, maximum):
        if diverged:
            Path('local.txt').write_text('local')
            self.commit(self.local)
        self.remote_change()
        calls = Counter()
        def probe(args, **kwargs):
            calls[args[0]] += 1
            return bg.probe(args, **kwargs)
        def run(args):
            calls[args[0]] += 1
            return bg.run_git(args)
        workflow = Engine(probe, run, lambda _: True, lambda _: None)
        with workflow.store.lock():
            operation = workflow.prepare()
            self.assertEqual(workflow.apply(operation), 0)
        self.assertLessEqual(sum(calls.values()), maximum, dict(calls))


class ReplayableMergeScenarios(unittest.TestCase):
    def test_disjoint_binary_unicode_and_newline_changes_preserve_data(self):
        for seed in range(6):
            with self.subTest(seed=seed):
                fixture = fixtures.RepositoryTests()
                fixture.setUp()
                try:
                    rng = random.Random(seed)
                    local_name = rng.choice(['本地.txt', 'local file.txt', 'local\nfile.bin'])
                    remote_name = rng.choice(['远端.txt', 'remote file.txt', 'remote\nfile.bin'])
                    local_bytes = bytes(rng.randrange(256) for _ in range(100))
                    remote_bytes = bytes(rng.randrange(256) for _ in range(100))
                    Path(local_name).write_bytes(local_bytes)
                    fixture.commit(fixture.local)
                    (fixture.other / remote_name).write_bytes(remote_bytes)
                    fixture.commit(fixture.other)
                    fixture.git('push', cwd=fixture.other)
                    workflow = bg.engine()
                    workflow.confirm = lambda _: True
                    operation = workflow.prepare()
                    self.assertEqual(workflow.apply(operation), 0)
                    self.assertEqual(Path(local_name).read_bytes(), local_bytes)
                    self.assertEqual(Path(remote_name).read_bytes(), remote_bytes)
                    self.assertEqual(fixture.git('rev-parse', operation['before_ref']), operation['before'])
                    self.assertEqual(workflow.reconcile(operation['id'])['classification'], 'completed')
                finally:
                    fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
