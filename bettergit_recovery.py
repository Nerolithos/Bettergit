"""Recover an existing managed checkpoint into a separate, detached worktree."""
import os
from pathlib import Path


def destination(engine, value):
    path = Path(os.path.abspath(os.path.expanduser(value)))
    path = path.parent.resolve() / path.name
    if os.path.lexists(path):
        raise RuntimeError('Recovery destination already exists; choose a new directory. Nothing will be overwritten.')
    if not path.parent.is_dir():
        raise RuntimeError('Recovery destination parent must already exist.')
    raw = engine.get(['worktree', 'list', '--porcelain', '-z'])
    for field in raw.split('\0'):
        if field.startswith('worktree '):
            existing = Path(field[len('worktree '):]).resolve()
            if path == existing or existing in path.parents:
                raise RuntimeError('Choose a destination outside existing worktrees.')
    return path


def recover_worktree(engine, operation_id, value, preview=True):
    summary = engine.recover(operation_id, preview=True)
    operation = engine.store.load(operation_id)
    path = destination(engine, value)
    summary.update(destination=str(path), detached=True, changes_current_worktree=False,
                   copies_uncommitted_files=False, created=False)
    if preview:
        return summary
    engine.say(f"Recover {summary['before'][:12]} into detached worktree {str(path)!r}; current files stay in place.")
    if not engine.confirm('Create this recovery worktree? Existing checkout hooks and filters may run.'):
        return summary
    if destination(engine, str(path)) != path:
        raise RuntimeError('Recovery destination changed; stopped.')
    verified = engine.recover(operation_id, preview=True)
    if verified['before'] != summary['before']:
        raise RuntimeError('Recovery checkpoint changed; stopped.')
    # Reserve a new directory exclusively. Never remove it automatically on
    # failure: hooks or another process may have written valuable content.
    attempt = {'path': str(path), 'commit': summary['before'], 'state': 'creating_or_interrupted'}
    attempts = operation.setdefault('recovery_worktrees', [])
    if not isinstance(attempts, list) or len(attempts) >= 100:
        raise RuntimeError('Recovery attempt records are invalid or at their limit; inspect history before retrying.')
    path.mkdir(mode=0o700)
    attempts.append(attempt)
    engine.store.save(operation)
    rc = engine.run_git(['worktree', 'add', '--detach', '--', str(path), summary['before']])
    attempt['exit_code'] = rc
    if rc:
        attempt['state'] = 'needs_review'
        engine.store.save(operation)
        raise RuntimeError('Recovery worktree creation failed. The destination was preserved; inspect it and git worktree list before retrying.')
    # Do not treat checkout-hook success as proof of the expected result.
    head_rc, head, _ = engine.probe(['-C', str(path), 'rev-parse', 'HEAD'])
    branch_rc, _, _ = engine.probe(['-C', str(path), 'symbolic-ref', '--quiet', 'HEAD'])
    status_rc, status, _ = engine.probe(['-C', str(path), '-c', 'core.fsmonitor=false', 'status', '--porcelain=v1', '-z', '--ignore-submodules=none'])
    root_rc, root, _ = engine.probe(['-C', str(path), 'rev-parse', '--show-toplevel'])
    common_rc, common, _ = engine.probe(['-C', str(path), 'rev-parse', '--path-format=absolute', '--git-common-dir'])
    clean = (root_rc == common_rc == 0 and Path(root.removesuffix('\n')).resolve() == path
             and Path(common.removesuffix('\n')).resolve() == Path(engine.common).resolve()) and head_rc == 0 and head.strip() == summary['before'] and branch_rc == 1 and status_rc == 0 and not status
    attempt['state'] = 'ready' if clean else 'needs_review'
    engine.store.save(operation)
    summary.update(created=True, state=attempt['state'])
    if not clean:
        engine.say('Recovery checkout exists but differs from the checkpoint or contains changes; inspect hooks and files before use.')
    else:
        engine.say('Recovery worktree verified. Inspect files there; your current worktree was not reset or switched.')
    return summary


def recovery_locations(engine, operation):
    """Read Git's registry without scanning unrelated checkout contents."""
    raw = engine.get(['worktree', 'list', '--porcelain', '-z'])
    registered, row = {}, {}
    for field in raw.split('\0'):
        if not field:
            if 'worktree' in row:
                registered[row['worktree']] = row
            row = {}
        else:
            key, _, value = field.partition(' ')
            row[key] = value
    attempts = operation.get('recovery_worktrees', [])
    if not isinstance(attempts, list):
        return [{'recorded_state': 'invalid_record'}]
    result = []
    for attempt in attempts[-20:]:
        if not isinstance(attempt, dict) or not isinstance(attempt.get('path'), str):
            result.append({'recorded_state': 'invalid_record'})
            continue
        path = attempt['path']
        entry = registered.get(path)
        result.append({'path': path, 'recorded_state': attempt.get('state', 'unknown'),
                       'registered': entry is not None, 'prunable': entry is not None and 'prunable' in entry,
                       'locked': entry is not None and 'locked' in entry,
                       'observed_head': entry.get('HEAD') if entry else None})
    return result


def history_detail(engine, operation_id):
    operation = engine.store.load(operation_id)
    inspection = engine.reconcile(operation_id)
    try:
        recovery = engine.recover(operation_id, preview=True)
        recovery['available'] = True
    except RuntimeError:
        recovery = {'available': False, 'reason': 'Checkpoint reference is missing or could not be verified'}
    return {'operation': operation, 'inspection': inspection, 'recovery': recovery,
            'recovery_worktrees': recovery_locations(engine, operation)}


def render_detail(detail, say):
    operation, inspection = detail['operation'], detail['inspection']
    say(f"Operation {operation['id']}: recorded={operation['state']}; observed={inspection['classification']}")
    say(f"Branch: {operation['branch']!r}; original worktree: {operation['root']!r}")
    publication = operation.get('publication')
    if isinstance(publication, dict):
        say('Publication record: ' + repr(publication.get('state', 'unknown')) + ' (historical observation; remote not queried)')
    say(f"Checkpoint: {operation['before'][:12]}; differing files: {len(operation['changed_files'])}")
    for path in operation['changed_files'][:20]:
        say('  ' + repr(path))
    if len(operation['changed_files']) > 20:
        say('  More paths are available with --json.')
    for action in inspection['suggested_actions']:
        say('Next: bettergit ' + action)
    for location in detail['recovery_worktrees']:
        say('Recovery checkout: ' + repr(location.get('path')) +
            '; registered=' + str(location.get('registered', False)) +
            '; prunable=' + str(location.get('prunable', False)) +
            '; locked=' + str(location.get('locked', False)) +
            '; recorded=' + repr(location['recorded_state']))
    if detail['recovery']['available']:
        say('Inspect safely: bettergit recover ' + operation['id'] + ' --worktree <new-directory>')
    else:
        say('Recovery unavailable: ' + detail['recovery']['reason'])
