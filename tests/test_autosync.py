import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch
import bettergit as bg
import test_bettergit as fixtures


class AutomaticSyncTests(unittest.TestCase):
    setUp = fixtures.RepositoryTests.setUp
    git = fixtures.RepositoryTests.git
    configure = fixtures.RepositoryTests.configure
    commit = fixtures.RepositoryTests.commit
    remote_change = fixtures.RepositoryTests.remote_change

    def remote_head(self):
        return self.git('ls-remote', 'origin', 'refs/heads/main').split()[0]

    def test_default_sync_adds_all_commits_version_and_pushes_without_prompt(self):
        count = int(self.git('rev-list','--count','--first-parent','HEAD'))
        Path('.gitignore').write_text('.env\n')
        Path('.env').write_text('ignored test input')
        Path('new.txt').write_text('new')
        Path('staged.txt').write_text('previously staged')
        self.git('add','staged.txt')
        Path('file.txt').unlink()
        with patch.object(bg,'confirm',side_effect=AssertionError('automatic sync must not prompt')):
            self.assertEqual(bg.main(['sync']),0)
        self.assertEqual(self.remote_head(),self.git('rev-parse','HEAD'))
        self.assertEqual(self.git('status','--porcelain'),'')
        self.assertNotIn('.env',self.git('ls-files').splitlines())
        self.assertNotIn('file.txt',self.git('ls-files').splitlines())
        message = self.git('log','-1','--format=%s')
        self.assertRegex(message,r'^Bettergit sync v0\.0\.'+str(count+1)+r' \| \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
        record = bg.engine().store.history()[0]
        self.assertEqual(record['snapshot_commit']['commit'],self.remote_head())
        self.assertEqual(record['publication']['state'],'verified')

    def test_clean_retry_does_not_create_empty_commit(self):
        Path('new.txt').write_text('new')
        self.assertEqual(bg.main(['sync','--push']),0)
        first = self.git('rev-parse','HEAD')
        self.assertEqual(bg.main(['sync']),0)
        self.assertEqual(self.git('rev-parse','HEAD'),first)

    def test_subdirectory_invocation_stages_whole_repository(self):
        Path('inside').mkdir(); Path('outside.txt').write_text('outside')
        os.chdir('inside')
        Path('inside.txt').write_text('inside')
        self.assertEqual(bg.main(['sync']),0)
        self.assertIn('outside.txt',self.git('ls-tree','-r','--name-only','HEAD',cwd=self.local).splitlines())
        self.assertEqual(self.remote_head(),self.git('rev-parse','HEAD'))

    def test_clean_divergence_is_merged_and_pushed_automatically(self):
        Path('local.txt').write_text('local snapshot')
        self.remote_change()
        with patch.object(bg,'confirm',side_effect=AssertionError('no prompt')):
            self.assertEqual(bg.main(['sync']),0)
        self.assertEqual(len(self.git('rev-list','--parents','-n','1','HEAD').split()),3)
        self.assertEqual(self.remote_head(),self.git('rev-parse','HEAD'))
        self.assertTrue(Path('local.txt').exists())
        self.assertTrue(Path('remote.txt').exists())

    def test_conflict_preserves_snapshot_without_entering_merge_or_pushing(self):
        Path('file.txt').write_text('local edit\n')
        self.remote_change('file.txt','remote edit\n')
        remote = self.remote_head()
        self.assertEqual(bg.main(['sync']),1)
        self.assertEqual(self.remote_head(),remote)
        self.assertFalse(Path('.git/MERGE_HEAD').exists())
        self.assertEqual(self.git('show','HEAD:file.txt'),'local edit')
        self.assertIn('Bettergit sync v0.0.',self.git('log','-1','--format=%s'))

    def test_failed_commit_preserves_staging_and_never_pushes(self):
        before = self.remote_head()
        Path('new.txt').write_text('new')
        hook=Path('.git/hooks/pre-commit');hook.write_text('#!/bin/sh\nexit 1\n');hook.chmod(0o755)
        self.assertEqual(bg.main(['sync']),1)
        self.assertEqual(self.remote_head(),before)
        self.assertIn('new.txt',self.git('diff','--cached','--name-only'))

    def test_existing_operation_is_not_automatically_committed(self):
        Path('new.txt').write_text('new')
        Path('.git/MERGE_HEAD').write_text(self.git('rev-parse','HEAD')+'\n')
        self.assertEqual(bg.main(['sync']),1)
        self.assertEqual(self.git('diff','--cached','--name-only'),'')
        self.assertTrue(Path('.git/MERGE_HEAD').exists())

    def test_destination_failure_happens_before_staging(self):
        Path('new.txt').write_text('new')
        self.git('remote','set-url','--push','origin',str(self.root/'wrong.git'))
        self.assertEqual(bg.main(['sync']),1)
        self.assertEqual(self.git('diff','--cached','--name-only'),'')

    def test_post_commit_changes_stop_publication(self):
        before=self.remote_head()
        Path('new.txt').write_text('new')
        hook=Path('.git/hooks/post-commit');hook.write_text('#!/bin/sh\nprintf edited > new.txt\n');hook.chmod(0o755)
        self.assertEqual(bg.main(['sync']),1)
        self.assertEqual(self.remote_head(),before)
        self.assertEqual(Path('new.txt').read_text(),'edited')

    def test_actual_noninteractive_cli_completes_pipeline(self):
        Path('new.txt').write_text('new')
        result=subprocess.run([sys.executable,'-B',str(Path(bg.__file__).resolve()),'sync'],stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.remote_head(),self.git('rev-parse','HEAD'))

    def test_plan_and_pull_do_not_automatically_commit_or_push(self):
        before=self.git('rev-parse','HEAD')
        Path('new.txt').write_text('new')
        self.assertEqual(bg.main(['sync','--plan']),1)
        self.assertEqual(bg.main(['run','--','pull']),1)
        self.assertEqual(self.git('rev-parse','HEAD'),before)
        self.assertEqual(self.git('diff','--cached','--name-only'),'')
