import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bettergit_pr import explain, load_snapshot, source, target, FILE_LIMIT
from pr_fixtures import HEAD, BASE, MERGE, check, status, snapshot, require_reviews


class ExplanationTests(unittest.TestCase):
    def codes(self, result):
        return {finding['code'] for finding in result['findings']}

    def test_ready_requires_complete_evidence(self):
        result = explain(snapshot())
        self.assertEqual(result['outcome'], 'ready')
        self.assertEqual(result['check_sha'], HEAD)
        self.assertTrue(result['read_only'])

    def test_missing_expected_check_is_reported_even_when_existing_checks_pass(self):
        data = snapshot()
        data['rules']['data'][0]['parameters']['required_status_checks'].append({'context': 'integration'})
        result = explain(data)
        self.assertEqual(result['outcome'], 'blocked')
        self.assertIn('required_check_missing', self.codes(result))
        self.assertEqual(result['required_checks'][1]['context'], 'integration')

    def test_status_and_check_with_same_name_must_both_pass(self):
        data = snapshot()
        data['statuses'][HEAD] = source([status(state='failure')])
        result = explain(data)
        self.assertEqual(result['outcome'], 'blocked')
        self.assertEqual(result['required_checks'][0]['verdict'], 'failed')

    def test_latest_check_rerun_supersedes_old_failure(self):
        data = snapshot()
        data['checks'][HEAD] = source([check(conclusion='failure', identifier=1), check(identifier=2)])
        self.assertEqual(explain(data)['outcome'], 'ready')

    def test_old_revision_is_never_used_as_a_passing_result(self):
        data = snapshot()
        data['checks'][HEAD] = source([check(sha='d' * 40)])
        result = explain(data)
        self.assertEqual(result['outcome'], 'unknown')
        self.assertIn('check_revision_mismatch', self.codes(result))

    def test_test_merge_revision_takes_precedence_when_it_has_results(self):
        data = snapshot()
        data['pull_request']['merge_commit_sha'] = MERGE
        data['observed_again'] = source(copy.deepcopy(data['pull_request']))
        data['checks'][MERGE] = source([check(sha=MERGE, conclusion='failure')])
        data['statuses'][MERGE] = source([])
        result = explain(data)
        self.assertEqual(result['check_sha'], MERGE)
        self.assertEqual(result['check_scope'], 'test_merge')
        self.assertEqual(result['outcome'], 'blocked')

    def test_empty_complete_test_merge_results_fall_back_to_head(self):
        data = snapshot()
        data['pull_request']['merge_commit_sha'] = MERGE
        data['observed_again'] = source(copy.deepcopy(data['pull_request']))
        data['checks'][MERGE], data['statuses'][MERGE] = source([]), source([])
        self.assertEqual(explain(data)['outcome'], 'ready')
        self.assertEqual(explain(data)['check_sha'], HEAD)

    def test_unavailable_merge_scope_is_unknown_not_head_success(self):
        data = snapshot()
        data['pull_request']['merge_commit_sha'] = MERGE
        data['observed_again'] = source(copy.deepcopy(data['pull_request']))
        result = explain(data)
        self.assertEqual(result['outcome'], 'unknown')
        self.assertIsNone(result['check_sha'])

    def test_partial_pagination_does_not_establish_success(self):
        data = snapshot()
        data['checks'][HEAD]['complete'] = False
        self.assertEqual(explain(data)['outcome'], 'unknown')

    def test_specific_app_cannot_be_satisfied_by_another_app(self):
        data = snapshot()
        data['rules']['data'][0]['parameters']['required_status_checks'][0]['integration_id'] = 20
        result = explain(data)
        self.assertEqual(result['outcome'], 'blocked')
        self.assertEqual(result['required_checks'][0]['verdict'], 'missing')

    def test_legacy_status_without_app_identity_cannot_prove_required_app(self):
        data = snapshot()
        data['rules']['data'][0]['parameters']['required_status_checks'][0]['integration_id'] = 20
        data['checks'][HEAD] = source([])
        data['statuses'][HEAD] = source([status()])
        self.assertEqual(explain(data)['outcome'], 'unknown')

    def test_invalid_app_identifier_does_not_weaken_a_requirement(self):
        data = snapshot()
        data['rules']['data'][0]['parameters']['required_status_checks'][0]['integration_id'] = 'wrong-type'
        self.assertEqual(explain(data)['outcome'], 'unknown')

    def test_missing_protection_permissions_are_not_treated_as_no_protection(self):
        data = snapshot()
        data['protection'] = source(status='unavailable', reason='permission_or_missing')
        self.assertEqual(explain(data)['outcome'], 'unknown')

    def test_review_count_is_not_a_substitute_for_github_review_eligibility(self):
        data = require_reviews(snapshot())
        data['reviews'] = source([{'id': 1, 'state': 'APPROVED', 'user': {'login': 'reviewer'}}])
        self.assertEqual(explain(data)['outcome'], 'unknown')
        data['review_summary']['data']['reviewDecision'] = 'APPROVED'
        self.assertEqual(explain(data)['outcome'], 'ready')

    def test_server_review_gate_blocks_even_with_observed_approvals(self):
        data = require_reviews(snapshot(), require_code_owner_review=True)
        data['reviews'] = source([{'id': 1, 'state': 'APPROVED', 'user': {'login': 'reviewer'}}])
        data['review_summary']['data']['reviewDecision'] = 'REVIEW_REQUIRED'
        result = explain(data)
        self.assertEqual(result['outcome'], 'blocked')
        self.assertIn('review_required', self.codes(result))

    def test_review_summary_and_reviews_disagreement_is_unknown(self):
        data = require_reviews(snapshot())
        data['review_summary']['data']['reviewDecision'] = 'APPROVED'
        self.assertEqual(explain(data)['outcome'], 'unknown')

    def test_dismissed_review_does_not_count_and_comment_does_not_revoke_approval(self):
        data = require_reviews(snapshot())
        data['reviews'] = source([{'id': 1, 'state': 'APPROVED', 'user': {'login': 'reviewer'}},
                                  {'id': 2, 'state': 'DISMISSED', 'user': {'login': 'reviewer'}}])
        self.assertEqual(explain(data)['reviews']['observed_approvals'], 0)
        data['reviews']['data'][1]['state'] = 'COMMENTED'
        data['review_summary']['data']['reviewDecision'] = 'APPROVED'
        self.assertEqual(explain(data)['outcome'], 'ready')

    def test_stale_pr_overrides_apparently_green_or_failed_old_results(self):
        data = snapshot()
        data['observed_again']['data']['head']['sha'] = 'd' * 40
        self.assertEqual(explain(data)['outcome'], 'unknown')
        data['checks'][HEAD]['data'][0]['conclusion'] = 'failure'
        self.assertEqual(explain(data)['outcome'], 'unknown')

    def test_review_summary_for_a_different_revision_is_not_trusted(self):
        data = snapshot()
        data['review_summary']['data']['headRefOid'] = 'd' * 40
        self.assertEqual(explain(data)['outcome'], 'unknown')

    def test_unverified_final_read_is_not_ready(self):
        data = snapshot()
        data['observed_again'] = source(status='unavailable', reason='timeout')
        self.assertEqual(explain(data)['outcome'], 'unknown')

    def test_final_read_failure_makes_old_blockers_unverified(self):
        data = snapshot()
        data['checks'][HEAD]['data'][0]['conclusion'] = 'failure'
        data['observed_again'] = source(status='unavailable', reason='timeout')
        self.assertEqual(explain(data)['outcome'], 'unknown')

    def test_missing_policy_parameters_and_draft_state_remain_unknown(self):
        data = snapshot()
        data['rules']['data'][0]['parameters'] = {}
        self.assertEqual(explain(data)['outcome'], 'unknown')
        data = snapshot()
        del data['pull_request']['draft']
        data['observed_again'] = source(copy.deepcopy(data['pull_request']))
        self.assertEqual(explain(data)['outcome'], 'unknown')

    def test_unsupported_rules_and_unresolved_thread_visibility_remain_unknown(self):
        data = snapshot()
        data['rules']['data'].append({'type': 'required_deployments'})
        self.assertEqual(explain(data)['outcome'], 'unknown')
        data = require_reviews(snapshot(), count=0, required_review_thread_resolution=True)
        data['review_summary']['data']['mergeStateStatus'] = 'BLOCKED'
        self.assertIn('conversation_resolution_unknown', self.codes(explain(data)))

    def test_merge_queue_is_explained_without_enqueuing(self):
        data = snapshot()
        data['rules']['data'].append({'type': 'merge_queue'})
        self.assertIn('merge_queue_required', self.codes(explain(data)))

    def test_draft_conflict_and_closed_pr_states(self):
        for field, value, code in [('draft', True, 'draft'), ('mergeable', False, 'merge_conflict')]:
            with self.subTest(field=field):
                data = snapshot()
                data['pull_request'][field] = value
                data['observed_again'] = source(copy.deepcopy(data['pull_request']))
                self.assertIn(code, self.codes(explain(data)))
        data = snapshot()
        data['pull_request']['state'] = 'closed'
        self.assertEqual(explain(data)['outcome'], 'not_open')

    def test_no_source_data_is_executed_or_rendered_as_terminal_control(self):
        data = snapshot()
        name = 'ci\x1b[31m; touch /tmp/should-never-run'
        data['rules']['data'][0]['parameters']['required_status_checks'] = [{'context': name}]
        with patch('bettergit_pr.capture', side_effect=AssertionError('analysis must not spawn commands')):
            result = explain(data)
        self.assertNotIn('\x1b', result['findings'][0]['message'])
        self.assertIn('required_check_missing', self.codes(result))

    def test_malformed_optional_sources_do_not_crash_or_turn_green(self):
        for field in ('rules', 'protection', 'checks', 'statuses', 'review_summary', 'observed_again'):
            with self.subTest(field=field):
                data = snapshot()
                data[field] = None
                result = explain(data)
                if field != 'review_summary':
                    self.assertNotEqual(result['outcome'], 'ready')


class InputTests(unittest.TestCase):
    def test_numeric_url_and_enterprise_targets(self):
        self.assertEqual(target('42', 'demo/project')['number'], 42)
        self.assertEqual(target('https://enterprise.example/demo/project/pull/42')['hostname'], 'enterprise.example')
        self.assertEqual(target('42', 'demo/project', 'enterprise.example:8443')['hostname'], 'enterprise.example:8443')

    def test_target_injection_credentials_and_conflicts_are_rejected(self):
        cases = [('https://token@github.com/demo/project/pull/1', None, None),
                 ('https://github.com/demo/project/pull/1?token=secret', None, None),
                 ('https://github.com/demo/project/pull/1', 'other/project', None),
                 ('42', '../project', None), ('0', 'demo/project', None),
                 ('42', 'demo/project', 'host;whoami'), ('42', None, None)]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(ValueError):
                target(*args)

    def test_snapshot_size_encoding_and_schema_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'snapshot.json'
            for content in (b'{}', b'\xff', b'x' * (FILE_LIMIT + 1)):
                with self.subTest(size=len(content)):
                    path.write_bytes(content)
                    with self.assertRaises(ValueError):
                        load_snapshot(path)
            path.write_text(json.dumps(snapshot()))
            self.assertEqual(explain(load_snapshot(path))['outcome'], 'ready')


if __name__ == '__main__':
    unittest.main()
