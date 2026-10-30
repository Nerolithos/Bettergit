"""Read-only PR explanations with pinned revisions and explicit visibility gaps."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

from bettergit_diagnostics import Budget
from bettergit_process import capture

OID = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}')
HOST = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?')
FILE_LIMIT = 4 * 1024 * 1024
PAGE_LIMIT = 5
ROW_LIMIT = 1000


def source(data=None, status='ok', reason=None):
    result = {'status': status, 'data': data, 'complete': status in ('ok', 'not_applicable')}
    if reason:
        result['reason'] = reason
    return result


def target(selector, repository=None, hostname=None):
    if isinstance(selector, str) and selector.startswith('https://'):
        parsed = urlsplit(selector)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Use a plain HTTPS PR URL without credentials, query parameters, or fragments.')
        parts = parsed.path.strip('/').split('/')
        if len(parts) != 4 or parts[2] != 'pull':
            raise ValueError('Expected a PR URL such as https://github.com/OWNER/REPO/pull/123.')
        host, owner, repo, number = parsed.netloc, parts[0], parts[1], parts[3]
        if repository and repository != owner + '/' + repo or hostname and hostname != host:
            raise ValueError('The PR URL conflicts with --repo or --hostname.')
    else:
        if not repository or len(repository.split('/')) != 2:
            raise ValueError('A numeric PR requires --repo OWNER/REPO.')
        owner, repo = repository.split('/')
        host, number = hostname or 'github.com', str(selector)
    if not HOST.fullmatch(host) or any(not NAME.fullmatch(value) for value in (owner, repo)):
        raise ValueError('Invalid repository or hostname.')
    if not number.isascii() or not number.isdigit() or not 0 < int(number) <= 2**31 - 1:
        raise ValueError('The PR number must be a positive integer.')
    return {'hostname': host.lower(), 'owner': owner, 'name': repo, 'number': int(number)}


def load_snapshot(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(FILE_LIMIT + 1)
    if len(raw) > FILE_LIMIT:
        raise ValueError('The PR snapshot exceeds the 4 MiB input limit.')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError('The PR snapshot is not valid UTF-8 JSON.')
    if not isinstance(data, dict) or data.get('schema') != 1:
        raise ValueError('Unsupported PR snapshot schema; expected schema 1.')
    return data


class Client:
    def __init__(self, repository, seconds=20, gh=None, run=capture):
        self.repository = repository
        self.budget = Budget(seconds)
        self.gh = gh or os.environ.get('BETTERGIT_GH', 'gh')
        self.run = run
        self.base = 'repos/' + repository['owner'] + '/' + repository['name']
        self.host = repository['hostname']

    def command(self, args):
        api_read = (args[:1] == ['api'] and '--method' in args
                    and args[args.index('--method') + 1:args.index('--method') + 2] == ['GET']
                    and not any(flag in args for flag in ('-F', '-f', '--field', '--raw-field', '--input')))
        pr_read = args[:2] == ['pr', 'view'] and len(args) == 7 and args[3] == '--repo' and args[5] == '--json'
        if not (api_read or pr_read):
            raise ValueError('The PR inspector only permits GET API reads and the fixed gh pr view query.')
        if self.budget.remaining() <= .01:
            return source(status='unavailable', reason='budget_exhausted')
        try:
            rc, out, err = self.run([self.gh, *args], timeout=self.budget.remaining(4),
                                    env={'GH_PROMPT_DISABLED': '1', 'GH_PAGER': 'cat', 'GH_DEBUG': ''})
        except FileNotFoundError:
            return source(status='unavailable', reason='gh_missing')
        if rc:
            reason = ('timeout' if rc == 124 else 'output_limit' if rc == 125 else
                      'permission_or_missing' if 'HTTP 403' in err or 'HTTP 404' in err else 'api_error')
            return source(status='unavailable', reason=reason)
        try:
            return source(json.loads(out))
        except (ValueError, UnicodeError):
            return source(status='unavailable', reason='invalid_json')

    def get(self, endpoint):
        return self.command(['api', '--hostname', self.host, '--method', 'GET',
                             '-H', 'Accept: application/vnd.github+json', endpoint])

    def pages(self, endpoint, key=None, sha=None):
        rows = []
        for page in range(1, PAGE_LIMIT + 1):
            response = self.get(endpoint + ('&' if '?' in endpoint else '?') + f'per_page=100&page={page}')
            if response['status'] != 'ok':
                return source(rows, status='incomplete', reason=response.get('reason', 'api_error'))
            data = response['data']
            if key:
                if not isinstance(data, dict):
                    return source(rows, status='incomplete', reason='invalid_shape')
                if key == 'statuses' and data.get('sha') != sha:
                    return source(rows, status='incomplete', reason='revision_mismatch')
                batch, total = data.get(key), data.get('total_count')
            else:
                batch, total = data, None
            if not isinstance(batch, list) or not all(isinstance(row, dict) for row in batch):
                return source(rows, status='incomplete', reason='invalid_shape')
            rows.extend(batch)
            if len(rows) > ROW_LIMIT:
                return source(rows[:ROW_LIMIT], status='incomplete', reason='record_limit')
            if len(batch) < 100 or isinstance(total, int) and len(rows) >= total:
                return source(rows)
        return source(rows, status='incomplete', reason='page_limit')

    def collect(self):
        number = self.repository['number']
        initial = self.get(self.base + f'/pulls/{number}')
        if initial.get('reason') == 'gh_missing':
            raise RuntimeError('GitHub CLI (gh) is required for live PR inspection. Install and authenticate gh, or use --from-json.')
        if initial['status'] != 'ok' or not isinstance(initial['data'], dict):
            raise RuntimeError('The pull request could not be read (' + initial.get('reason', 'invalid_shape') + '). Check gh authentication, repository access, and the PR number.')
        pr = initial['data']
        validate_pr(pr)
        if pr['number'] != number:
            raise RuntimeError('The PR response does not match the requested PR number.')
        branch = quote(pr['base']['ref'], safe='')
        branch_info = self.get(self.base + '/branches/' + branch)
        rules = self.pages(self.base + '/rules/branches/' + branch)
        if branch_info['status'] == 'ok' and isinstance(branch_info['data'], dict) and branch_info['data'].get('protected') is False:
            protection = source({}, status='not_applicable')
        else:
            protection = self.get(self.base + '/branches/' + branch + '/protection')
        checks, statuses = {}, {}
        revisions = [pr['head']['sha']]
        merge = pr.get('merge_commit_sha')
        if isinstance(merge, str) and OID.fullmatch(merge) and merge not in revisions and not pr.get('merged'):
            revisions.append(merge)
        for sha in revisions:
            checks[sha] = self.pages(self.base + '/commits/' + sha + '/check-runs?filter=latest', 'check_runs', sha)
            statuses[sha] = self.pages(self.base + '/commits/' + sha + '/status', 'statuses', sha)
        # gh pr view executes a read-only GraphQL query; it does not review/merge a PR.
        review_summary = self.command(['pr', 'view', str(number), '--repo', self.host + '/' + self.repository['owner'] + '/' + self.repository['name'],
                                       '--json', 'headRefOid,baseRefOid,reviewDecision,mergeStateStatus'])
        reviews = self.pages(self.base + f'/pulls/{number}/reviews')
        final = self.get(self.base + f'/pulls/{number}')
        return {'schema': 1, 'collected_at': datetime.now(timezone.utc).isoformat(),
                'repository': self.repository, 'pull_request': pr, 'branch': branch_info,
                'rules': rules, 'protection': protection, 'checks': checks, 'statuses': statuses,
                'reviews': reviews, 'review_summary': review_summary, 'observed_again': final}


def validate_pr(pr):
    if not isinstance(pr, dict) or not isinstance(pr.get('number'), int) or isinstance(pr.get('number'), bool) or pr['number'] <= 0:
        raise ValueError('The PR snapshot has no valid PR number.')
    for side in ('head', 'base'):
        if not isinstance(pr.get(side), dict) or not isinstance(pr[side].get('sha'), str) or not OID.fullmatch(pr[side]['sha']):
            raise ValueError('The PR snapshot has an invalid head/base revision.')
    if not isinstance(pr['base'].get('ref'), str) or not pr['base']['ref']:
        raise ValueError('The PR snapshot has no base branch name.')


def items(value):
    """Return bounded records and completeness; never turn invalid data into success."""
    if not isinstance(value, dict) or value.get('status') != 'ok' or value.get('complete') is not True:
        data = value.get('data') if isinstance(value, dict) else None
        return ([row for row in data[:ROW_LIMIT] if isinstance(row, dict)] if isinstance(data, list) else []), False
    data = value.get('data')
    if not isinstance(data, list):
        return [], False
    return [row for row in data[:ROW_LIMIT] if isinstance(row, dict)], len(data) <= ROW_LIMIT and all(isinstance(row, dict) for row in data)


def latest(rows, key):
    result = {}
    for row in rows:
        identifier = key(row)
        value = row.get('id', 0)
        rank = value if isinstance(value, int) else 0
        previous = result.get(identifier)
        old_id = previous.get('id', 0) if previous else -1
        if not isinstance(old_id, int):
            old_id = 0
        if previous is None or rank >= old_id:
            result[identifier] = row
    return list(result.values())


def explain(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('schema') != 1:
        raise ValueError('Unsupported PR snapshot schema; expected schema 1.')
    pr = snapshot.get('pull_request')
    validate_pr(pr)
    head, base = pr['head']['sha'], pr['base']['sha']
    result = {'schema': 1, 'kind': 'pr_explanation', 'number': pr['number'], 'read_only': True,
              'head_sha': head, 'base_sha': base, 'check_sha': None, 'check_scope': None,
              'outcome': 'unknown', 'findings': [], 'required_checks': [],
              'limitations': ['This is a bounded, non-atomic observation of GitHub state.',
                             'It does not merge, enqueue, approve, rerun CI, or change local Git state.',
                             'Permissions, unsupported rules, and partial pagination remain unknown.']}
    findings = result['findings']
    def add(code, level, message, evidence=None, next_step=None):
        if len(findings) >= 256:
            result['findings_truncated'] = True
            return
        findings.append({'code': code, 'level': level, 'message': message,
                         'evidence': evidence or {}, 'next_step': next_step or 'Inspect the PR and repeat after the relevant state changes.'})
    if pr.get('state') == 'closed' or pr.get('merged'):
        result['outcome'] = 'not_open'
        add('pr_not_open', 'info', 'This PR is merged or closed; merge eligibility is not evaluated.')
        return result
    if pr.get('state') != 'open':
        add('pr_state_unknown', 'unknown', 'The PR open/closed state is unavailable.')
    if not isinstance(pr.get('draft'), bool):
        add('draft_state_unknown', 'unknown', 'The draft state is unavailable.')
    if pr.get('draft'):
        add('draft', 'blocker', 'The PR is a draft.', next_step='Mark it ready for review when appropriate.')
    if pr.get('mergeable') is False or pr.get('mergeable_state') == 'dirty':
        add('merge_conflict', 'blocker', 'GitHub reports a merge conflict.', next_step='Inspect the conflict in a separate worktree; this command does not resolve it.')
    elif pr.get('mergeable') is not True:
        add('mergeability_unknown', 'unknown', 'GitHub has not provided a definite mergeability result.')

    rules, rules_complete = items(snapshot.get('rules'))
    protection_source = snapshot.get('protection') if isinstance(snapshot.get('protection'), dict) else {}
    protection = protection_source.get('data') if isinstance(protection_source, dict) else None
    protection_complete = (isinstance(protection, dict) and protection_source.get('status') in ('ok', 'not_applicable') and protection_source.get('complete') is True)
    protection = protection if isinstance(protection, dict) else {}
    if not rules_complete or not protection_complete:
        add('policy_visibility', 'unknown', 'Some applicable rules or classic branch protection could not be fully read.',
            {'rules_complete': rules_complete, 'protection_complete': protection_complete},
            'Check API access. A 403/404 is not treated as proof that no protection exists.')
    requirements = set()
    review_count, codeowners, last_push, conversations, strict = 0, False, False, False, False
    def require(name, app=None):
        if not isinstance(name, str) or not name or len(name) > 500:
            add('invalid_requirement', 'unknown', 'A required-check definition is invalid.')
            return
        if len(requirements) >= 256:
            add('requirement_limit', 'unknown', 'Required-check inspection exceeded its record limit.')
            return
        if app is not None and (not isinstance(app, int) or isinstance(app, bool) or app < -1):
            add('invalid_requirement', 'unknown', 'A required-check App identifier is invalid.')
            return
        requirements.add((name, app if isinstance(app, int) and app > 0 else None))
    def review_policy(policy):
        nonlocal review_count, codeowners, last_push, conversations
        if not isinstance(policy, dict):
            add('invalid_review_policy', 'unknown', 'Review policy data is incomplete.')
            return
        count = policy.get('required_approving_review_count')
        if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 100:
            review_count = max(review_count, count)
        else:
            add('invalid_review_policy', 'unknown', 'The required approval count is invalid.')
        codeowners |= bool(policy.get('require_code_owner_review') or policy.get('require_code_owner_reviews'))
        last_push |= bool(policy.get('require_last_push_approval'))
        conversations |= bool(policy.get('required_review_thread_resolution'))
    for rule in rules:
        kind = rule.get('type')
        params = rule.get('parameters') or {}
        if not isinstance(params, dict):
            add('invalid_rule', 'unknown', 'An active rule has invalid parameters.')
            continue
        if kind == 'required_status_checks':
            definitions = params.get('required_status_checks')
            if not isinstance(definitions, list):
                add('invalid_requirement', 'unknown', 'Required-check definitions are unavailable.')
                continue
            if len(definitions) > 256:
                add('requirement_limit', 'unknown', 'Required-check definitions exceed the inspection limit.')
            for row in definitions[:257]:
                if isinstance(row, dict):
                    require(row.get('context'), row.get('integration_id'))
                else:
                    add('invalid_requirement', 'unknown', 'A required-check definition is invalid.')
            strict |= bool(params.get('strict_required_status_checks_policy'))
        elif kind == 'pull_request':
            review_policy(params)
        elif kind == 'merge_queue':
            add('merge_queue_required', 'blocker', 'Repository rules require the merge queue for merging.', next_step='Use the repository merge queue after resolving other requirements; this command does not enqueue.')
        elif kind in ('deletion', 'non_fast_forward', 'creation'):
            pass  # These rules alone do not explain whether an open PR is ready.
        else:
            add('unsupported_rule', 'unknown', 'An active rule requires additional verification.', {'rule_type': str(kind)[:100]})
    classic = protection.get('required_status_checks') or {}
    if isinstance(classic, dict):
        checks = classic.get('checks', [])
        if not isinstance(checks, list) or len(checks) > 256:
            add('invalid_requirement', 'unknown', 'Classic check definitions are invalid or exceed the inspection limit.')
        if isinstance(checks, list):
            for row in checks[:257]:
                if isinstance(row, dict):
                    require(row.get('context'), row.get('app_id'))
                else:
                    add('invalid_requirement', 'unknown', 'A classic check definition is invalid.')
        contexts = classic.get('contexts', [])
        if not isinstance(contexts, list) or len(contexts) > 256:
            add('invalid_requirement', 'unknown', 'Classic check contexts are invalid or exceed the inspection limit.')
        if isinstance(contexts, list):
            for name in contexts[:257]:
                if not any(existing == name for existing, _ in requirements):
                    require(name)
        strict |= bool(classic.get('strict'))
    else:
        add('invalid_requirement', 'unknown', 'Classic required-check policy is invalid.')
    classic_reviews = protection.get('required_pull_request_reviews')
    if classic_reviews is not None:
        review_policy(classic_reviews)
    resolution = protection.get('required_conversation_resolution') or {}
    conversations |= isinstance(resolution, dict) and resolution.get('enabled') is True

    check_sources = snapshot.get('checks') if isinstance(snapshot.get('checks'), dict) else {}
    status_sources = snapshot.get('statuses') if isinstance(snapshot.get('statuses'), dict) else {}
    merge = pr.get('merge_commit_sha')
    selected = head
    if isinstance(merge, str) and OID.fullmatch(merge) and merge != head:
        merge_checks, mc = items(check_sources.get(merge))
        merge_statuses, ms = items(status_sources.get(merge))
        if merge_checks or merge_statuses:
            selected = merge
        elif not (mc and ms):
            selected = None
            if requirements:
                add('check_scope_unknown', 'unknown', 'Test-merge observations are incomplete; required checks cannot be confidently attributed to head or test merge.')
    if selected:
        result['check_sha'] = selected
        result['check_scope'] = 'head' if selected == head else 'test_merge'
        checks, cc = items(check_sources.get(selected))
        statuses, sc = items(status_sources.get(selected))
        if any(row.get('head_sha') != selected for row in checks):
            cc = False
            add('check_revision_mismatch', 'unknown', 'Some check runs belong to a different revision and were excluded.')
        checks = [row for row in checks if row.get('head_sha') == selected]
        check_key = lambda row: (str(row.get('name')), row['app'].get('id') if isinstance(row.get('app'), dict) and isinstance(row['app'].get('id'), int) else None)
        checks = latest(checks, check_key)
        statuses = latest(statuses, lambda row: str(row.get('context')))
        for name, app in sorted(requirements, key=lambda row: (row[0], row[1] or 0)):
            matching = [row for row in checks if row.get('name') == name and (app is None or isinstance(row.get('app'), dict) and row['app'].get('id') == app)]
            legacy = [row for row in statuses if row.get('context') == name]
            states = []
            for row in matching:
                status, conclusion = row.get('status'), row.get('conclusion')
                states.append('pass' if status == 'completed' and conclusion in ('success', 'neutral', 'skipped') else
                              'fail' if status == 'completed' and conclusion in ('failure', 'timed_out', 'cancelled', 'action_required', 'startup_failure', 'stale') else
                              'pending' if status in ('queued', 'in_progress', 'waiting', 'requested', 'pending') else 'unknown')
            if app is None:
                states.extend('pass' if row.get('state') == 'success' else 'fail' if row.get('state') in ('failure', 'error') else
                              'pending' if row.get('state') == 'pending' else 'unknown' for row in legacy)
            verdict = ('failed' if 'fail' in states else 'pending' if 'pending' in states else 'unknown' if 'unknown' in states else
                       'passed' if states else 'unknown' if app and legacy else 'missing' if cc and sc else 'unknown')
            if verdict == 'passed' and not (cc and sc):
                verdict = 'unknown'
            row = {'context': name, 'app_id': app, 'sha': selected, 'verdict': verdict}
            result['required_checks'].append(row)
            if verdict != 'passed':
                add('required_check_' + verdict, 'unknown' if verdict == 'unknown' else 'blocker',
                    f'Required check {name!r} is {verdict} on the observed revision.', row,
                    'Inspect the expected workflow/App and its trigger; a missing result may reflect filtering or a check that never ran.')
    if strict:
        state = pr.get('mergeable_state')
        if state == 'behind':
            add('branch_out_of_date', 'blocker', 'The branch must be brought up to date with its base branch.')
        elif state != 'clean':
            add('base_sync_unknown', 'unknown', 'Strict up-to-date policy exists, but its current satisfaction is not established.')

    summary_source = snapshot.get('review_summary') if isinstance(snapshot.get('review_summary'), dict) else {}
    summary = summary_source.get('data') if isinstance(summary_source, dict) else None
    valid_summary = (isinstance(summary, dict) and summary_source.get('status') == 'ok' and summary_source.get('complete') is True
                     and summary.get('headRefOid') == head and summary.get('baseRefOid') == base)
    decision = summary.get('reviewDecision') if valid_summary else None
    if decision not in (None, '', 'APPROVED', 'CHANGES_REQUESTED', 'REVIEW_REQUIRED'):
        add('review_decision_unknown', 'unknown', 'The review-decision value is unsupported.')
        decision = None
    reviews, reviews_complete = items(snapshot.get('reviews'))
    decisive = [row for row in reviews if row.get('state') in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED') and isinstance(row.get('user'), dict) and isinstance(row['user'].get('login'), str)]
    approvals = sum(row.get('state') == 'APPROVED' for row in latest(decisive, lambda row: row['user']['login']))
    result['reviews'] = {'required_approval_count': review_count, 'observed_approvals': approvals,
                         'code_owner_required': codeowners, 'last_push_approval_required': last_push,
                         'server_decision': decision, 'complete': reviews_complete}
    if decision == 'CHANGES_REQUESTED':
        add('changes_requested', 'blocker', 'GitHub reports changes requested by reviewers.')
    elif review_count or codeowners or last_push:
        if decision == 'REVIEW_REQUIRED':
            add('review_required', 'blocker', 'GitHub reports that required review approval is still missing.', result['reviews'])
        elif decision == 'APPROVED' and reviews_complete and approvals < review_count:
            add('review_evidence_conflict', 'unknown', 'The review summary and approval observations disagree; repeat the inspection.')
        elif decision != 'APPROVED':
            if reviews_complete and approvals < review_count:
                add('approval_count', 'blocker', 'Observed approvals are below the required count.', result['reviews'])
            else:
                add('review_eligibility_unknown', 'unknown', 'Observed approval count alone does not prove reviewer eligibility or code-owner/last-push approval.', result['reviews'])
    if conversations:
        server_state = summary.get('mergeStateStatus') if valid_summary else None
        if server_state != 'CLEAN':
            add('conversation_resolution_unknown', 'unknown', 'Conversation resolution is required, but individual review-thread resolution has not been collected.')
    if pr.get('mergeable_state') == 'blocked' and not any(f['level'] == 'blocker' for f in findings):
        add('server_blocked', 'blocker', 'GitHub reports this PR blocked; available evidence does not fully explain the blocker.')

    final_source = snapshot.get('observed_again') if isinstance(snapshot.get('observed_again'), dict) else {}
    final = final_source.get('data') if isinstance(final_source, dict) else None
    if final_source.get('status') != 'ok' or final_source.get('complete') is not True or not isinstance(final, dict):
        result['snapshot_unverified'] = True
        add('snapshot_unverified', 'unknown', 'The PR could not be re-read after inspection; revision stability is unverified.')
    else:
        signature = lambda row: (row.get('number'), (row['head'].get('sha') if isinstance(row.get('head'), dict) else None), (row['base'].get('sha') if isinstance(row.get('base'), dict) else None),
                                  (row['base'].get('ref') if isinstance(row.get('base'), dict) else None), row.get('merge_commit_sha'), row.get('state'), row.get('draft'), row.get('updated_at'))
        if signature(final) != signature(pr):
            result['snapshot_changed'] = True
            add('snapshot_changed', 'unknown', 'The PR changed during inspection; these findings apply to the captured revision. Repeat for the current PR.')
    if isinstance(summary, dict) and summary_source.get('status') == 'ok' and not valid_summary:
        result['snapshot_changed'] = True
        add('review_revision_mismatch', 'unknown', 'Review-summary revision differs from the captured head/base revision.')
    if pr.get('mergeable_state') not in ('clean', 'unstable', 'blocked', 'dirty', 'behind'):
        add('server_state_unknown', 'unknown', 'The server merge-state assessment is unavailable or still being calculated.')
    if pr.get('mergeable_state') == 'behind' and not strict:
        add('base_sync_unknown', 'unknown', 'The server reports a behind branch, but its update policy is not fully established.')
    result['outcome'] = ('unknown' if result.get('snapshot_changed') or result.get('snapshot_unverified') or result.get('findings_truncated') else 'blocked' if any(f['level'] == 'blocker' for f in findings) else
                         'unknown' if any(f['level'] == 'unknown' for f in findings) else 'ready')
    if result['outcome'] == 'ready':
        add('observed_requirements_satisfied', 'info', 'All observed and supported requirements are satisfied for this snapshot; no merge action was taken.')
    return result


def render(result, say):
    say(f"PR #{result['number']}: {result['outcome']}")
    say(f"Head: {result['head_sha'][:12]}; checks: {result['check_scope'] or 'unknown'} {str(result['check_sha'] or '')[:12]}")
    for finding in result['findings']:
        say(f"[{finding['level']}] {finding['message']}")
        if finding['level'] != 'info':
            say('  Next: ' + finding['next_step'])
