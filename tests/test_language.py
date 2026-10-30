"""English defaults are part of the CLI and testing contract."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_bettergit as fixtures

ROOT = Path(__file__).resolve().parents[1]


class LanguageTests(unittest.TestCase):
    def test_help_and_argument_errors_use_english(self):
        for args, code, phrase in [(['--help'], 0, 'Git diagnostics'),
                                   (['doctor', '--remote', 'origin'], 2, 'requires --network'),
                                   (['doctor', '--experiment', 'untracked-cache', '--network'], 2, 'cannot be combined')]:
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, str(ROOT / 'bettergit.py'), *args], capture_output=True, text=True)
                self.assertEqual(result.returncode, code)
                output = result.stdout + result.stderr
                self.assertIn(phrase, output)
                self.assertNotRegex(output, '[\u4e00-\u9fff]')

    def test_configuration_validation_message_is_english(self):
        env = dict(os.environ, BETTERGIT_SLOW_SECONDS='-1')
        result = subprocess.run([sys.executable, str(ROOT / 'bettergit.py'), 'run', '--', 'status'], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('finite positive number', result.stderr)
        self.assertNotRegex(result.stderr, '[\u4e00-\u9fff]')

    def test_readme_is_english_and_has_portable_quick_start(self):
        text = (ROOT / 'README.md').read_text()
        self.assertNotRegex(text, '[\u4e00-\u9fff]')
        self.assertNotIn('/Users/Lithos/', text)
        self.assertIn('source "$PWD/bettergit.zsh"', text)


class ReportLanguageTests(unittest.TestCase):
    setUp = fixtures.RepositoryTests.setUp
    git = fixtures.RepositoryTests.git
    configure = fixtures.RepositoryTests.configure
    commit = fixtures.RepositoryTests.commit
    remote_change = fixtures.RepositoryTests.remote_change

    def test_json_diagnostics_use_english_narrative(self):
        result = subprocess.run([sys.executable, str(ROOT / 'bettergit.py'), 'doctor', '--samples', '1', '--json'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        for finding in report['findings']:
            self.assertNotRegex(json.dumps(finding, ensure_ascii=False), '[\u4e00-\u9fff]')
        self.assertTrue(report['limitations'])


if __name__ == '__main__':
    unittest.main()
