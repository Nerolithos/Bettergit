from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bettergit_pr import Client, explain, target, source
from pr_fixtures import HEAD, BASE, check, snapshot


class FakeGh:
    def __init__(self):
        self.calls = []
        self.pr = snapshot()['pull_request']
        self.reads = 0
        self.errors = {}
        self.check_count = 1
        self.wrong_status_sha = False
        self.change_final_head = False

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[1:3] == ['pr', 'view']:
            data = {'headRefOid': HEAD, 'baseRefOid': BASE, 'reviewDecision': None, 'mergeStateStatus': 'CLEAN'}
            return 0, json.dumps(data), ''
        assert '--method' in command and command[command.index('--method') + 1] == 'GET'
        endpoint = command[-1]
        for key, response in self.errors.items():
            if key in endpoint:
                return response
        path, _, query = endpoint.partition('?')
        if path.endswith('/pulls/42'):
            self.reads += 1
            data = deepcopy(self.pr)
            if self.change_final_head and self.reads == 2:
                data['head']['sha'] = 'd' * 40
        elif '/rules/branches/' in path:
            data = snapshot()['rules']['data']
        elif '/branches/' in path:
            data = {'protected': False}
        elif path.endswith('/check-runs'):
            page = int(query.split('page=')[-1])
            first, last = (page - 1) * 100, min(page * 100, self.check_count)
            rows = [check(name='ci' if i == 0 else 'optional-' + str(i), identifier=i + 1) for i in range(first, last)]
            data = {'total_count': self.check_count, 'check_runs': rows}
        elif path.endswith('/status'):
            data = {'sha': 'd' * 40 if self.wrong_status_sha else HEAD, 'total_count': 0, 'statuses': []}
        elif path.endswith('/reviews'):
            data = []
        else:
            raise AssertionError('Unexpected read endpoint: ' + endpoint)
        return 0, json.dumps(data), ''


class ClientTests(unittest.TestCase):
    def test_collection_is_read_only_pinned_and_skips_inapplicable_admin_api(self):
        fake = FakeGh()
        captured = Client(target('42', 'demo/project'), run=fake).collect()
        self.assertEqual(explain(captured)['outcome'], 'ready')
        self.assertEqual(fake.reads, 2)
        self.assertFalse(any('/protection' in command[-1] for command, _ in fake.calls))
        self.assertTrue(all(command[1:3] == ['pr', 'view'] or command[command.index('--method') + 1] == 'GET' for command, _ in fake.calls))
        self.assertTrue(all(kwargs['env']['GH_DEBUG'] == '' for _, kwargs in fake.calls))

    def test_pagination_completes_and_caps_without_turning_partial_data_green(self):
        fake = FakeGh()
        fake.check_count = 150
        captured = Client(target('42', 'demo/project'), run=fake).collect()
        self.assertTrue(captured['checks'][HEAD]['complete'])
        self.assertEqual(len(captured['checks'][HEAD]['data']), 150)
        fake = FakeGh()
        fake.check_count = 501
        captured = Client(target('42', 'demo/project'), run=fake).collect()
        self.assertFalse(captured['checks'][HEAD]['complete'])
        self.assertEqual(len(captured['checks'][HEAD]['data']), 500)
        self.assertEqual(explain(captured)['outcome'], 'unknown')

    def test_permission_errors_are_unknown_and_raw_secrets_are_not_reported(self):
        fake = FakeGh()
        fake.errors['/rules/branches/'] = (1, '', 'HTTP 403 secret-token-value')
        captured = Client(target('42', 'demo/project'), run=fake).collect()
        result = explain(captured)
        self.assertEqual(result['outcome'], 'unknown')
        self.assertNotIn('secret-token-value', json.dumps(captured) + json.dumps(result))

    def test_moving_head_during_live_collection_invalidates_results(self):
        fake = FakeGh()
        fake.change_final_head = True
        self.assertEqual(explain(Client(target('42', 'demo/project'), run=fake).collect())['outcome'], 'unknown')

    def test_mismatched_combined_status_sha_is_incomplete(self):
        fake = FakeGh()
        fake.wrong_status_sha = True
        captured = Client(target('42', 'demo/project'), run=fake).collect()
        self.assertEqual(captured['statuses'][HEAD]['reason'], 'revision_mismatch')
        self.assertEqual(explain(captured)['outcome'], 'unknown')

    def test_branch_name_is_encoded_as_data_not_endpoint_syntax(self):
        fake = FakeGh()
        fake.pr['base']['ref'] = 'release/next'
        Client(target('42', 'demo/project'), run=fake).collect()
        endpoints = [command[-1] for command, _ in fake.calls if command[1] == 'api']
        self.assertTrue(any('/branches/release%2Fnext' in endpoint for endpoint in endpoints))

    def test_missing_gh_is_actionable(self):
        def missing(*args, **kwargs):
            raise FileNotFoundError
        with self.assertRaisesRegex(RuntimeError, 'Install and authenticate gh'):
            Client(target('42', 'demo/project'), run=missing).collect()

    def test_mutating_commands_are_rejected_before_execution(self):
        fake = FakeGh()
        client = Client(target('42', 'demo/project'), run=fake)
        for args in (['pr', 'merge', '42'], ['api', '--method', 'POST', 'repos/demo/project/pulls/42/merge'], ['auth', 'login']):
            with self.subTest(args=args), self.assertRaises(ValueError):
                client.command(args)
        self.assertEqual(fake.calls, [])

    def test_shared_budget_is_enforced_without_firing_more_requests(self):
        fake = FakeGh()
        client = Client(target('42', 'demo/project'), seconds=.000001, run=fake)
        client.budget.end = 0
        with self.assertRaises(RuntimeError):
            client.collect()
        self.assertEqual(fake.calls, [])

    def test_requested_pr_identity_is_checked(self):
        fake = FakeGh()
        fake.pr['number'] = 99
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            Client(target('42', 'demo/project'), run=fake).collect()

    def test_timeout_output_limit_and_bad_json_do_not_turn_green(self):
        for response in ((124, '', 'secret'), (125, '', 'secret'), (0, '{broken', '')):
            with self.subTest(response=response):
                fake = FakeGh()
                fake.errors['/check-runs'] = response
                captured = Client(target('42', 'demo/project'), run=fake).collect()
                self.assertEqual(explain(captured)['outcome'], 'unknown')
                self.assertNotIn('secret', json.dumps(captured))

    def test_cli_live_adapter_uses_only_read_operations_with_a_fake_gh_binary(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            payload = directory / 'snapshot.json'
            data = snapshot()
            data['rules']['data'][0]['parameters']['required_status_checks'].append({'context': 'integration'})
            payload.write_text(json.dumps(data))
            calls = directory / 'calls.jsonl'
            fake = directory / 'gh'
            fake.write_text('#!' + sys.executable + '\n' + r'''import json,os,sys
args=sys.argv[1:]
with open(os.environ['TEST_GH_CALLS'],'a') as log: log.write(json.dumps(args)+'\n')
data=json.load(open(os.environ['TEST_GH_DATA']))
if args[:2]==['pr','view']:
 print(json.dumps(data['review_summary']['data'])); raise SystemExit
assert args[0]=='api' and args[args.index('--method')+1]=='GET'
path=args[-1].split('?')[0]
if path.endswith('/pulls/42'): result=data['pull_request']
elif '/rules/branches/' in path: result=data['rules']['data']
elif '/branches/' in path: result={'protected':False}
elif path.endswith('/check-runs'): result={'check_runs':data['checks'][data['pull_request']['head']['sha']]['data'],'total_count':1}
elif path.endswith('/status'): result={'sha':data['pull_request']['head']['sha'],'statuses':[],'total_count':0}
elif path.endswith('/reviews'): result=[]
else: raise RuntimeError('Unexpected endpoint')
print(json.dumps(result))
''')
            fake.chmod(0o755)
            env = dict(os.environ, BETTERGIT_GH=str(fake), TEST_GH_CALLS=str(calls), TEST_GH_DATA=str(payload))
            process = subprocess.run([sys.executable, str(root / 'bettergit.py'), 'pr', 'explain', '42', '--repo', 'demo/project', '--json'],
                                     cwd=directory, env=env, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout)['outcome'], 'blocked')
            commands = [json.loads(line) for line in calls.read_text().splitlines()]
            self.assertTrue(all(cmd[:2] == ['pr', 'view'] or cmd[cmd.index('--method') + 1] == 'GET' for cmd in commands))
            self.assertFalse((directory / '.git').exists())

    def test_cli_offline_path_never_needs_gh_and_preserves_english_output(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'snapshot.json'
            path.write_text(json.dumps(snapshot()))
            env = dict(os.environ, BETTERGIT_GH='/missing/gh')
            process = subprocess.run([sys.executable, str(root / 'bettergit.py'), 'pr', 'explain', '--from-json', str(path), '--json'],
                                     env=env, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout)['outcome'], 'ready')
            self.assertNotRegex(process.stdout + process.stderr, '[\u4e00-\u9fff]')


if __name__ == '__main__':
    unittest.main()
