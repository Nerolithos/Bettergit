"""Small synthetic PR snapshots; no account, network, or private data required."""
from copy import deepcopy
from bettergit_pr import source

HEAD = 'a' * 40
BASE = 'b' * 40
MERGE = 'c' * 40


def check(name='ci', conclusion='success', sha=HEAD, identifier=1, app=10, status='completed'):
    return {'id': identifier, 'name': name, 'head_sha': sha, 'status': status,
            'conclusion': conclusion, 'app': {'id': app}}


def status(name='ci', state='success', identifier=1):
    return {'id': identifier, 'context': name, 'state': state}


def snapshot():
    pr = {'number': 42, 'state': 'open', 'draft': False, 'merged': False,
          'mergeable': True, 'mergeable_state': 'clean', 'merge_commit_sha': None,
          'updated_at': '2026-10-30T00:00:00Z',
          'head': {'sha': HEAD}, 'base': {'sha': BASE, 'ref': 'main'}}
    return {'schema': 1, 'repository': {'hostname': 'github.com', 'owner': 'demo', 'name': 'project', 'number': 42},
            'pull_request': pr, 'branch': source({'protected': False}),
            'rules': source([{'type': 'required_status_checks', 'parameters': {'required_status_checks': [{'context': 'ci'}]}}]),
            'protection': source({}, status='not_applicable'),
            'checks': {HEAD: source([check()])}, 'statuses': {HEAD: source([])},
            'reviews': source([]),
            'review_summary': source({'headRefOid': HEAD, 'baseRefOid': BASE, 'reviewDecision': None, 'mergeStateStatus': 'CLEAN'}),
            'observed_again': source(deepcopy(pr))}


def require_reviews(data, count=1, **options):
    data['rules']['data'].append({'type': 'pull_request', 'parameters': {'required_approving_review_count': count, **options}})
    return data


def missing_example():
    data = snapshot()
    data['rules']['data'][0]['parameters']['required_status_checks'].append({'context': 'integration'})
    return data
