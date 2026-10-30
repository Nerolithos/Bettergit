import contextlib
import io
import os
from pathlib import Path
import subprocess
import signal
import time
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bettergit as bg


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = self.root / 'remote.git'
        self.local = self.root / 'local'
        self.other = self.root / 'other'
        self.git('init', '--bare', str(self.remote), cwd=self.root)
        self.git('clone', str(self.remote), str(self.local), cwd=self.root)
        self.configure(self.local)
        (self.local / 'file.txt').write_text('base\n')
        self.commit(self.local)
        self.git('push', '-u', 'origin', 'HEAD', cwd=self.local)
        self.git('clone', str(self.remote), str(self.other), cwd=self.root)
        self.configure(self.other)
        self.previous = Path.cwd()
        os.chdir(self.local)
        self.addCleanup(os.chdir, self.previous)

    def git(self, *args, cwd=None):
        return subprocess.check_output(['git', *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True).strip()

    def configure(self, cwd):
        self.git('config', 'user.name', 'Test', cwd=cwd)
        self.git('config', 'user.email', 'test@example.invalid', cwd=cwd)
        self.git('config', 'commit.gpgsign', 'false', cwd=cwd)
        # A private/empty Git template need not create this directory.
        hooks = Path(self.git('rev-parse', '--git-path', 'hooks', cwd=cwd))
        (Path(cwd) / hooks).mkdir(parents=True, exist_ok=True)

    def commit(self, cwd):
        self.git('add', '.', cwd=cwd)
        self.git('commit', '-m', 'test', cwd=cwd)

    def remote_change(self, name='remote.txt', content='remote\n'):
        (self.other / name).write_text(content)
        self.commit(self.other)
        self.git('push', cwd=self.other)

    def test_fast_forward_requires_confirmation(self):
        head = self.git('rev-parse', 'HEAD')
        self.remote_change()
        with patch.object(bg, 'confirm', return_value=False) as ask:
            self.assertEqual(bg.sync(), 0)
            ask.assert_called_once()
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        with patch.object(bg, 'confirm', return_value=True):
            self.assertEqual(bg.sync(), 0)
        self.assertTrue(Path('remote.txt').exists())

    def test_diverged_clean_merge(self):
        Path('local.txt').write_text('local')
        self.commit(self.local)
        self.remote_change()
        with patch.object(bg, 'confirm', return_value=True):
            self.assertEqual(bg.sync(), 0)
        self.assertEqual(len(self.git('rev-list', '--parents', '-n', '1', 'HEAD').split()), 3)
        self.assertTrue(Path('local.txt').exists())
        self.assertTrue(Path('remote.txt').exists())

    def test_conflict_report_preserves_index_and_worktree(self):
        Path('file.txt').write_text('local\n')
        self.commit(self.local)
        self.remote_change('file.txt', 'remote\n')
        head = self.git('rev-parse', 'HEAD')
        index = Path('.git/index').read_bytes()
        err = io.StringIO()
        with contextlib.redirect_stderr(err), patch.object(bg, 'confirm') as ask:
            self.assertEqual(bg.sync(), 1)
            ask.assert_not_called()
        self.assertIn('file.txt', err.getvalue())
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertEqual(Path('file.txt').read_text(), 'local\n')
        self.assertEqual(Path('.git/index').read_bytes(), index)
        self.assertFalse(Path('.git/MERGE_HEAD').exists())

    def test_dirty_workspace_blocks_merge(self):
        self.remote_change()
        Path('file.txt').write_text('uncommitted')
        with patch.object(bg, 'confirm') as ask:
            self.assertEqual(bg.sync(), 1)
            ask.assert_not_called()

    def test_confirmation_race_blocks_merge(self):
        self.remote_change()
        def change(_):
            Path('new.txt').write_text('changed')
            return True
        with patch.object(bg, 'confirm', side_effect=change):
            self.assertEqual(bg.sync(), 1)
        self.assertFalse(Path('remote.txt').exists())

    def test_check_never_merges(self):
        self.remote_change()
        self.git('fetch')
        with patch.object(bg, 'confirm') as ask:
            self.assertEqual(bg.sync(fetch=False), 0)
            ask.assert_not_called()
        self.assertFalse(Path('remote.txt').exists())

    def test_existing_gitignore_preserved(self):
        Path('.gitignore').write_text('custom\n')
        with patch.object(bg, 'confirm', return_value=True):
            bg.init_ignore(['init'])
        self.assertEqual(Path('.gitignore').read_text(), 'custom\n')

    def test_init_template_detection(self):
        Path('pyproject.toml').touch()
        with patch.object(bg, 'confirm', return_value=True), patch('sys.stdin.isatty', return_value=True):
            bg.init_ignore(['init'])
        self.assertIn('.venv/', Path('.gitignore').read_text())

    def test_trace_evidence_redacts_credentials(self):
        trace = self.root / 'trace'
        trace.write_text('{"event":"child_start","sid":"1","child_id":0,"argv":["git-remote-https", "https://secret@example.com"]}\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            bg.diagnose(trace)
        self.assertIn('remote transport', err.getvalue())
        self.assertNotIn('secret', err.getvalue())


class RunnerTests(unittest.TestCase):
    def test_slow_command_triggers_once_and_preserves_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'fakegit'
            fake.write_text('#!/bin/sh\nsleep 0.15\nexit 7\n')
            fake.chmod(0o755)
            with patch.object(bg, 'GIT', str(fake)), patch.dict(os.environ, BETTERGIT_SLOW_SECONDS='0.02'), patch.object(bg, 'diagnose') as diagnose:
                self.assertEqual(bg.run_git(['status']), 7)
                diagnose.assert_called_once()

    def test_ctrl_c_exits_130_and_stops_foreground_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp) / 'ready'
            fake = Path(tmp) / 'git'
            fake.write_text('#!' + sys.executable + '\nimport pathlib,time,os\npathlib.Path(os.environ["TEST_READY"]).touch()\ntime.sleep(30)\n')
            fake.chmod(0o755)
            env = dict(os.environ, BETTERGIT_GIT=str(fake), TEST_READY=str(ready), BETTERGIT_SLOW_SECONDS='20')
            p = subprocess.Popen([sys.executable, str(Path(bg.__file__).resolve()), 'run', '--', 'status'],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, start_new_session=True)
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and p.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready.exists())
                os.killpg(p.pid, signal.SIGINT)
                p.communicate(timeout=6)
                self.assertEqual(p.returncode, 130)
            finally:
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.communicate()

    def test_invalid_thresholds(self):
        for value in ['0', '-1', 'nan', 'inf', 'wrong']:
            with self.subTest(value=value), patch.dict(os.environ, BETTERGIT_SLOW_SECONDS=value):
                with self.assertRaises(ValueError):
                    bg.threshold()

    def test_no_typo_correction(self):
        with patch.object(bg, 'run_git', return_value=1) as run:
            self.assertEqual(bg.main(['run', '--', 'stauts']), 1)
            run.assert_called_once_with(['stauts'])

    def test_noninteractive_confirmation_is_no(self):
        with patch('sys.stdin.isatty', return_value=False):
            self.assertFalse(bg.confirm('Merge?'))


if __name__ == '__main__':
    unittest.main()
