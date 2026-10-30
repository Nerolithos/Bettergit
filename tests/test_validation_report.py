import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from scripts.validation_report import metrics_gate, report, run_stage, write_json


class ValidationReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def evidence(self, count=20, matched=True):
        write_json(self.directory / 'workflow-measurement.json', [
            {'scenario': 'fast_forward', 'plan_git_invocations': count, 'apply_git_invocations': 0},
            {'scenario': 'diverged_clean_merge', 'plan_git_invocations': 16, 'apply_git_invocations': 14}])
        write_json(self.directory / 'diagnostic-evaluation.json', {'passed': matched, 'cases': [{'matched': matched}]})

    def summary(self, passed=True):
        write_json(self.directory / 'summary.json', {
            'passed': passed, 'complete': True, 'measure_requested': True,
            'isolated_config_unchanged': True, 'protected_user_configs_unchanged': True,
            'checks': [{'name': name, 'status': 'passed', 'exit_code': 0, 'seconds': .1}
                       for name in ('versions','unittest','workflow_measurement','diagnostic_evaluation','quality_gates','zsh_syntax','zsh_smoke')]})
        (self.directory / 'unittest.log').write_text('Ran 164 tests in 1.0s\nOK\n')

    def test_success_report_reuses_actual_evidence(self):
        self.evidence(); self.summary()
        self.assertTrue(metrics_gate(self.directory)['passed'])
        text, passed = report(self.directory)
        self.assertTrue(passed)
        self.assertIn('20 / maximum 25', text)
        self.assertIn('Tests executed: **164**', text)

    def test_regression_and_mismatched_diagnosis_fail_gate(self):
        self.evidence(count=26, matched=False)
        result = metrics_gate(self.directory)
        self.assertFalse(result['passed'])
        self.assertEqual(len(result['issues']), 2)

    def test_missing_invalid_duplicate_and_boolean_evidence_fail(self):
        self.assertFalse(metrics_gate(self.directory)['passed'])
        self.evidence()
        rows = json.loads((self.directory / 'workflow-measurement.json').read_text())
        for modified in ([rows[0],rows[0]], [dict(rows[0],plan_git_invocations=True),rows[1]], [], None):
            write_json(self.directory / 'workflow-measurement.json', modified)
            self.assertFalse(metrics_gate(self.directory)['passed'])

    def test_incomplete_or_missing_summary_never_passes(self):
        self.assertFalse(report(self.directory)[1])
        self.evidence(); metrics_gate(self.directory); self.summary()
        data = json.loads((self.directory / 'summary.json').read_text())
        data['complete'] = False; data['checks'][0]['status'] = 'running'
        write_json(self.directory / 'summary.json', data)
        text, passed = report(self.directory)
        self.assertFalse(passed)
        self.assertIn('running', text)

    def test_failed_tests_are_named_and_markdown_is_escaped(self):
        self.evidence(); metrics_gate(self.directory); self.summary(passed=False)
        (self.directory / 'unittest.log').write_text('FAIL: test_bad <script>alert(1)</script>|\nRan 1 test in 0.1s\nFAILED\n')
        text, passed = report(self.directory)
        self.assertFalse(passed)
        self.assertIn('test_bad', text)
        self.assertNotIn('<script>', text)
        self.assertIn('&#124;', text)

    def test_timeout_keeps_log_and_stops_same_group_child(self):
        heartbeat = self.directory / 'heartbeat'
        child = 'import pathlib,time,sys\nwhile True:\n pathlib.Path(sys.argv[1]).write_text(str(time.time()))\n time.sleep(.02)'
        parent = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",sys.argv[1],sys.argv[2]]); time.sleep(30)'
        started = time.monotonic()
        result = run_stage('slow', [sys.executable,'-c',parent,child,str(heartbeat)], self.directory, self.directory, dict(os.environ), timeout=.5)
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(result['exit_code'], 124)
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(heartbeat.exists())
        before = heartbeat.read_bytes(); time.sleep(.1)
        self.assertEqual(before, heartbeat.read_bytes())
        self.assertIn('time budget', (self.directory/'slow.log').read_text())

    def test_failure_and_unavailable_executable_are_recorded(self):
        failed = run_stage('failed', [sys.executable,'-c','raise SystemExit(7)'], self.directory, self.directory, dict(os.environ))
        self.assertEqual(failed['exit_code'], 7)
        missing = run_stage('missing', [str(self.directory/'no-command')], self.directory, self.directory, dict(os.environ))
        self.assertEqual(missing['status'], 'launch_failed')
        self.assertEqual(missing['exit_code'], 127)

    def test_cli_writes_github_summary_and_preserves_failure(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/validation_report.py'
        target = self.directory/'github-summary.md'
        process = subprocess.run([sys.executable,'-B',str(script),'--input',str(self.directory),'--github-summary'],
                                 env=dict(os.environ,GITHUB_STEP_SUMMARY=str(target)),capture_output=True,text=True)
        self.assertEqual(process.returncode, 1)
        self.assertIn('INCOMPLETE',target.read_text())
        self.assertTrue((self.directory/'report.md').exists())

    def test_missing_stage_or_changed_configuration_cannot_pass(self):
        self.evidence(); metrics_gate(self.directory); self.summary()
        data = json.loads((self.directory / 'summary.json').read_text())
        data['checks'].pop()
        write_json(self.directory / 'summary.json', data)
        self.assertFalse(report(self.directory)[1])
        self.summary()
        data = json.loads((self.directory / 'summary.json').read_text())
        data['protected_user_configs_unchanged'] = False
        write_json(self.directory / 'summary.json', data)
        self.assertFalse(report(self.directory)[1])

    def test_real_runner_preserves_failure_and_completes_remaining_stages(self):
        root = Path(__file__).resolve().parents[1]
        fixture = self.directory/'fixture'; (fixture/'tests').mkdir(parents=True)
        (fixture/'tests/test_failure.py').write_text('import unittest\nclass Failure(unittest.TestCase):\n def test_deliberate_failure(self): self.assertEqual(1,2)\n')
        for name in ('bettergit.zsh','smart-edit-min.zsh'):
            (fixture/name).symlink_to(root/name)
        output = self.directory/'evidence'
        code = ('import sys; from pathlib import Path; sys.path.insert(0,' + repr(str(root/'scripts')) + '); '
                'import test_isolated; test_isolated.ROOT=Path(' + repr(str(fixture)) + '); '
                'sys.argv=["runner","--output",' + repr(str(output)) + ']; raise SystemExit(test_isolated.main())')
        result = subprocess.run([sys.executable,'-B','-c',code], capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,1,result.stderr)
        summary = json.loads((output/'summary.json').read_text())
        self.assertTrue(summary['complete'])
        self.assertFalse(summary['passed'])
        self.assertEqual(next(c for c in summary['checks'] if c['name']=='unittest')['status'],'failed')
        self.assertEqual(summary['checks'][-1]['name'],'zsh_smoke')
        self.assertTrue(summary['protected_user_configs_unchanged'])
        text, passed = report(output)
        self.assertFalse(passed)
        self.assertIn('test_deliberate_failure',text)
