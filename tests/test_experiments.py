import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_bettergit as fixtures
import bettergit_experiments as exp
from bettergit_process import capture


class ExperimentTests(unittest.TestCase):
    setUp = fixtures.RepositoryTests.setUp
    git = fixtures.RepositoryTests.git
    configure = fixtures.RepositoryTests.configure
    commit = fixtures.RepositoryTests.commit
    remote_change = fixtures.RepositoryTests.remote_change

    def test_paired_measurement_preserves_index_config_refs_and_files(self):
        Path('file.txt').write_text('unstaged content')
        Path('staged.txt').write_text('staged content')
        self.git('add', 'staged.txt')
        Path('untracked.txt').write_text('untracked content')
        before_index = Path('.git/index').read_bytes()
        before_config = Path('.git/config').read_bytes()
        before_refs = self.git('for-each-ref', '--format=%(refname) %(objectname)')
        before_files = {p.name: p.read_bytes() for p in Path('.').iterdir() if p.is_file()}
        report = exp.experiment(samples=3, seconds=10)
        self.assertEqual(report['state'], 'completed', report)
        self.assertTrue(report['original_index_unchanged'])
        self.assertTrue(report['configuration_unchanged'])
        self.assertTrue(report['status_outputs_consistent'])
        arms = [m['arm'] for m in report['measurements'] if m['phase'] == 'measured']
        self.assertEqual(arms, ['off', 'on', 'on', 'off', 'off', 'on'])
        self.assertEqual(Path('.git/index').read_bytes(), before_index)
        self.assertEqual(Path('.git/config').read_bytes(), before_config)
        self.assertEqual(self.git('for-each-ref', '--format=%(refname) %(objectname)'), before_refs)
        self.assertEqual({p.name: p.read_bytes() for p in Path('.').iterdir() if p.is_file()}, before_files)

    def test_split_index_is_explicitly_unsupported_and_unchanged(self):
        self.git('update-index', '--split-index')
        before = Path('.git/index').read_bytes()
        report = exp.experiment(samples=1)
        self.assertEqual(report['state'], 'unsupported_layout')
        self.assertEqual(Path('.git/index').read_bytes(), before)

    def test_external_filters_are_not_executed(self):
        marker = self.root / 'filter-executed'
        self.git('config', 'filter.test.clean', 'touch ' + str(marker))
        before = Path('.git/config').read_bytes()
        report = exp.experiment(samples=1)
        self.assertEqual(report['state'], 'unsupported_filters')
        self.assertFalse(marker.exists())
        self.assertEqual(Path('.git/config').read_bytes(), before)

    def test_fsmonitor_is_disabled_without_changing_its_configuration(self):
        marker = self.root / 'fsmonitor-executed'
        helper = self.root / 'fsmonitor'
        helper.write_text('#!/bin/sh\ntouch ' + str(marker) + '\nexit 1\n')
        helper.chmod(0o755)
        self.git('config', 'core.fsmonitor', str(helper))
        before = Path('.git/config').read_bytes()
        report = exp.experiment(samples=1)
        self.assertEqual(report['state'], 'completed', report)
        self.assertFalse(marker.exists())
        self.assertEqual(Path('.git/config').read_bytes(), before)
        self.assertEqual(report['recommendation']['kind'], 'inconclusive')

    def test_concurrent_native_index_update_is_detected_and_preserved(self):
        changed = False
        def concurrent(command, **kwargs):
            nonlocal changed
            result = capture(command, **kwargs)
            if 'status' in command and not changed:
                changed = True
                Path('native.txt').write_text('native Git staged this')
                self.git('add', 'native.txt')
            return result
        with patch.object(exp, 'capture', side_effect=concurrent):
            report = exp.experiment(samples=1)
        self.assertEqual(report['state'], 'source_changed')
        self.assertFalse(report['original_index_unchanged'])
        self.assertIn('native.txt', self.git('diff', '--cached', '--name-only'))

    def test_shared_budget_expiry_is_reported_without_a_recommendation(self):
        report = exp.experiment(samples=1, seconds=.000001)
        self.assertNotEqual(report['state'], 'completed')
        self.assertEqual(report['recommendation']['kind'], 'inconclusive')

    def test_existing_index_lock_is_never_removed(self):
        lock = Path('.git/index.lock')
        lock.touch()
        self.assertEqual(exp.experiment(samples=1)['state'], 'git_busy')
        self.assertTrue(lock.exists())

    def test_cli_json_and_english_messages(self):
        cli = str(Path(__file__).resolve().parents[1] / 'bettergit.py')
        result = subprocess.run([sys.executable, cli, 'doctor', '--experiment', 'untracked-cache', '--samples', '1', '--json'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['kind'], 'experiment')
        self.assertIn('at least three', report['recommendation']['message'])
        self.assertNotRegex(result.stdout, '[\u4e00-\u9fff]')

    def test_invalid_budget_or_samples_fail_before_touching_index(self):
        before = Path('.git/index').read_bytes()
        for seconds, samples in [(0, 3), (float('nan'), 3), (5, 0), (5, 6)]:
            with self.subTest(seconds=seconds, samples=samples), self.assertRaises(ValueError):
                exp.experiment(seconds=seconds, samples=samples)
        self.assertEqual(Path('.git/index').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
