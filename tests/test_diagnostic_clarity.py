import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import bettergit_diagnostics as diag
import test_bettergit as fixtures


class ClassificationTests(unittest.TestCase):
    def test_specific_remote_failures_have_distinct_actions(self):
        cases = [
            ('fatal: unable to access: Could not resolve proxy: proxy.invalid', 'proxy_failure'),
            ('fatal: could not resolve host https://example.invalid/repo', 'dns_failure'),
            ('ssh: Could not resolve hostname example.invalid: nodename nor servname provided', 'dns_failure'),
            ('Host key verification failed.', 'ssh_host_key_failure'),
            ('WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!', 'ssh_host_key_failure'),
            ('Permission denied (publickey).', 'authentication_failure'),
            ('fatal: Authentication failed', 'authentication_failure'),
            ('SSL certificate problem: unable to get local issuer certificate', 'tls_failure'),
            ('connect to host: Connection refused', 'connection_refused'),
            ('fatal: repository not found', 'remote_repository_unavailable'),
            ('fatal: server returned a novel failure', 'remote_read_failed'),
        ]
        for error, code in cases:
            with self.subTest(code=code):
                result = diag.remote_failure(128, error + ' secret-token')
                self.assertEqual(result[0], code)
                self.assertNotIn('secret-token', json.dumps(result))
                self.assertTrue(result[2])

    def test_timeout_and_output_cap_do_not_claim_network_fault(self):
        self.assertEqual(diag.remote_failure(124, 'authentication failed')[0], 'network_timeout')
        self.assertIn('unknown', diag.remote_failure(124, '')[1])
        self.assertEqual(diag.remote_failure(125, '')[0], 'remote_output_limit')
        self.assertEqual(diag.remote_failure(0, 'old error')[0], 'remote_reachable')

    def test_failed_probe_cannot_produce_complete_assessment(self):
        report = {'measurements': [{'name': 'status_normal', 'status': 'timeout'}], 'findings': []}
        diag.finalize(report)
        self.assertEqual(report['assessment']['state'], 'incomplete')
        self.assertFalse(report['coverage']['complete'])
        lines = []
        diag.render(report, lines.append)
        self.assertIn('Incomplete probes: status_normal', lines)

    def test_success_is_not_a_claim_that_all_causes_are_identified(self):
        report = {'measurements': [{'name': 'status_normal', 'status': 'ok'}], 'findings': []}
        diag.finalize(report)
        self.assertEqual(report['assessment']['state'], 'inconclusive')


class ProbeIsolationTests(unittest.TestCase):
    setUp = fixtures.RepositoryTests.setUp
    git = fixtures.RepositoryTests.git
    configure = fixtures.RepositoryTests.configure
    commit = fixtures.RepositoryTests.commit
    remote_change = fixtures.RepositoryTests.remote_change

    def test_real_fsmonitor_is_not_executed_and_index_is_preserved(self):
        marker = Path('monitor-ran').resolve()
        helper = Path('monitor.sh').resolve()
        helper.write_text('#!/bin/sh\nprintf x > "' + str(marker) + '"\nexit 1\n')
        helper.chmod(0o755)
        self.git('config', 'core.fsmonitor', str(helper))
        before = Path('.git/index').read_bytes()
        report = diag.collect(seconds=5, samples=1)
        self.assertFalse(marker.exists())
        self.assertEqual(Path('.git/index').read_bytes(), before)
        self.assertTrue(report['coverage']['complete'])
        self.assertTrue(any('fsmonitor' in x for x in report['limitations']))

    def test_failed_status_does_not_become_no_clear_bottleneck(self):
        original = diag.capture
        def failed(command, **kwargs):
            if 'status' in command:
                return 125, '', 'private error'
            return original(command, **kwargs)
        with patch.object(diag, 'capture', side_effect=failed):
            report = diag.collect(seconds=5, samples=1)
        self.assertFalse(report['coverage']['complete'])
        self.assertIn('status_no', report['coverage']['incomplete_probes'])
        self.assertEqual(report['assessment']['state'], 'incomplete')
        self.assertNotIn('private error', json.dumps(report))
