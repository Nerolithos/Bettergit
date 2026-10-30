"""Durable merge plans, repository-wide locking, backup refs and recovery.

The lock coordinates bettergit processes, not arbitrary external Git clients.
Recovery creates a branch; it never silently resets the current worktree.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from bettergit_snapshot import RepositorySnapshot, worktree

MARKERS = ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'sequencer')


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, common):
        self.path = Path(common) / 'bettergit'

    @contextmanager
    def lock(self):
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path / 'operation.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Another bettergit operation is running; retry later.')
            yield
        finally:
            os.close(fd)

    def save(self, operation):
        operation['updated_at'] = timestamp()
        atomic_json(self.path / 'operations' / (operation['id'] + '.json'), operation)

    def load(self, operation_id):
        if not re.fullmatch('[0-9a-f]{32}', operation_id):
            raise RuntimeError('Invalid operation ID.')
        try:
            data = json.loads((self.path / 'operations' / (operation_id + '.json')).read_text())
        except (FileNotFoundError, ValueError):
            raise RuntimeError('The operation record is missing or corrupt.')
        string_fields = ('id', 'kind', 'state', 'created_at', 'root', 'gitdir', 'common', 'before',
                         'target', 'branch_ref', 'branch', 'remote', 'upstream', 'config_digest', 'mergeability')
        if (not isinstance(data, dict) or data.get('schema') != 1 or data.get('id') != operation_id
                or any(not isinstance(data.get(key), str) for key in string_fields)
                or not isinstance(data.get('ahead'), int) or not isinstance(data.get('behind'), int)
                or not isinstance(data.get('changed_files'), list) or not isinstance(data.get('conflicts'), list)
                or any(not isinstance(p, str) for p in data['changed_files'] + data['conflicts'])
                or any(not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', data[key]) for key in ('before', 'target'))
                or data['kind'] != 'merge'
                or data['state'] not in ('preparing', 'planned', 'up_to_date', 'conflicted', 'running', 'failed', 'interrupted', 'succeeded', 'needs_review', 'aborted')
                or data['mergeability'] not in ('up_to_date', 'fast_forward', 'conflicted', 'clean_merge')
                or (data['mergeability'] == 'clean_merge' and (not isinstance(data.get('preview_tree'), str) or not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', data['preview_tree'])))
                or 'push_target' not in data
                or (data['push_target'] is not None and (not isinstance(data['push_target'], dict)
                    or any(not isinstance(data['push_target'].get(k), str) for k in ('remote', 'ref'))))):
            raise RuntimeError('The operation record has an unsupported format.')
        return data

    def history(self):
        result = []
        for path in sorted((self.path / 'operations').glob('*.json')):
            try:
                result.append(self.load(path.stem))
            except RuntimeError:
                result.append({'id': path.stem, 'state': 'unreadable'})
        return sorted(result, key=lambda row: row.get('created_at', ''), reverse=True)


class Engine:
    def __init__(self, probe, run_git, confirm, say, checkpoint=None):
        self.probe, self.run_git, self.confirm, self.say = probe, run_git, confirm, say
        self.checkpoint = checkpoint or (lambda name, operation: None)
        raw = self.get(['rev-parse', '--path-format=absolute', '--show-toplevel', '--absolute-git-dir', '--git-common-dir'])
        paths = raw.split('\n')
        if len(paths) == 3 and all(Path(p).is_absolute() for p in paths):
            self.root, self.gitdir, common = paths
        else:
            # Newlines are legal in filesystem paths; do not guess delimiters.
            self.root = self.get(['rev-parse', '--show-toplevel'])
            self.gitdir = self.get(['rev-parse', '--absolute-git-dir'])
            common = self.get(['rev-parse', '--path-format=absolute', '--git-common-dir'])
        self.common = str(Path(common).resolve())
        self.store = Store(self.common)
        # Only immutable object-to-object diffs are cached, not repository state.
        self._changes = {}

    def get(self, args):
        rc, out, _ = self.probe(args, timeout=15)
        if rc:
            raise RuntimeError(f'Git {args[0]} check failed (exit {rc}); check the repository, branch, and upstream.')
        return out.removesuffix('\n')

    def marker(self, name):
        # These operation markers are per-worktree, including linked worktrees.
        # Avoid spawning six rev-parse processes on every preflight.
        return Path(self.gitdir) / name

    def active(self):
        return [name for name in MARKERS if self.marker(name).exists()]

    def require_clean(self, state=None):
        state = state or worktree(self)
        if state.active:
            raise RuntimeError('A Git operation is already in progress; finish or abort it first.')
        if state.dirty:
            raise RuntimeError('The worktree or index is not clean; commit or stash changes before merging.')
        return state

    def changes(self, before, result):
        key = (before, result)
        if key not in self._changes:
            raw = self.get(['diff', '--no-ext-diff', '--no-textconv', '--no-renames',
                            '--name-status', '-z', before, result])
            fields = raw.split('\0')
            if fields[-1:] == ['']:
                fields.pop()
            if len(fields) % 2:
                raise RuntimeError('Could not parse all target-tree changes; merge stopped.')
            self._changes[key] = tuple(zip(fields[::2], fields[1::2]))
        return self._changes[key]

    def protect_new_paths(self, before, result):
        """Protect existing untracked/ignored content without traversing generated dirs.

        merge --no-overwrite-ignore alone did not protect an ort merge in our
        Git 2.45.2 regression. Inspect additions in the exact projected tree.
        This check cannot exclude arbitrary writes by external concurrent tools.
        """
        changes = self.changes(before, result)
        deletions = {p for status, p in changes if status == 'D'}
        for status, name in changes:
            if status != 'A':
                continue
            path = Path(name)
            if path.is_absolute() or '..' in path.parts:
                raise RuntimeError('A target-tree path escapes the repository.')
            # Inspect ancestors first; never follow a symlink into another tree.
            for parent in reversed(path.parents[:-1]):
                absolute = Path(self.root) / parent
                if absolute.is_symlink():
                    raise RuntimeError(f'A new path traverses the existing symbolic link {str(parent)!r}; inspect it first.')
                if absolute.is_file() and str(parent) not in deletions:
                    raise RuntimeError(f'A target directory overlaps the local file {str(parent)!r}; merge stopped.')
            if os.path.lexists(Path(self.root) / path):
                raise RuntimeError(f'A new target path overlaps existing local content {name!r} (possibly ignored); merge stopped.')

    def context(self):
        return RepositorySnapshot.read(self).context()

    def preview(self, head, target, ahead, behind):
        if not behind:
            return {'mergeability': 'up_to_date', 'conflicts': []}
        if not ahead:
            return {'mergeability': 'fast_forward', 'conflicts': []}
        rc, out, _ = self.probe(['merge-tree', '--write-tree', '--name-only', '-z', head, target], timeout=15)
        if rc == 1:
            conflicts = []
            for path in out.split('\0')[1:]:
                if not path:
                    break
                conflicts.append(path)
            return {'mergeability': 'conflicted', 'conflicts': conflicts}
        if rc != 0:
            raise RuntimeError('Could not complete the merge preview: unsupported Git, unrelated histories, driver failure, output limit, or timeout; merge stopped.')
        tree = out.split('\0', 1)[0]
        if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', tree):
            raise RuntimeError('The merge preview returned an invalid tree ID; merge stopped.')
        return {'mergeability': 'clean_merge', 'conflicts': [], 'preview_tree': tree}

    def backup(self, operation_id, label, commit):
        ref = f'refs/bettergit/operations/{operation_id}/{label}'
        self.get(['update-ref', '-m', 'bettergit recovery point', ref, commit, '0' * len(commit)])
        return ref

    def prepare(self, fetch=True, persist=True):
        initial = RepositorySnapshot.read(self)
        self.require_clean(initial.state)
        context, head = initial.context(), initial.state.head
        if fetch:
            rc = self.run_git(['fetch', '--', context['remote']])
            if rc:
                raise RuntimeError(f'Fetch failed (exit {rc}); no merge was attempted.')
            current = RepositorySnapshot.read(self)
        else:
            self.say('Analyzing the cached upstream only; the remote may have changed.')
            current = initial
        if current.state.head != head or current.context() != context:
            raise RuntimeError('The branch or configuration changed during fetch or inspection; create a new plan.')
        self.require_clean(current.state)
        target = current.refs.get(context['upstream'])
        if not target:
            raise RuntimeError('The upstream reference is missing; check the tracking configuration.')
        ahead, behind = map(int, self.get(['rev-list', '--left-right', '--count', f'{head}...{target}']).split())
        preview = self.preview(head, target, ahead, behind)
        if ahead and behind:
            after_preview = RepositorySnapshot.read(self)
            self.require_clean(after_preview.state)
            if (after_preview.state.head != head or after_preview.context() != context
                    or after_preview.refs.get(context['upstream']) != target):
                raise RuntimeError('The branch, upstream, or configuration changed during preview; create a new plan.')
        if preview['mergeability'] in ('fast_forward', 'clean_merge'):
            self.protect_new_paths(head, preview.get('preview_tree', target))
        changed = [p for _, p in self.changes(head, target)]
        operation = dict(schema=1, id=uuid.uuid4().hex, created_at=timestamp(), state='planned',
                         kind='merge', root=self.root, gitdir=self.gitdir, common=self.common,
                         before=head, target=target, ahead=ahead, behind=behind, fetched=fetch,
                         changed_files=changed, **context, **preview)
        ready = 'conflicted' if preview['mergeability'] == 'conflicted' else 'up_to_date' if not behind else 'planned'
        operation['state'] = ready
        if persist:
            operation['state'] = 'preparing'
            operation['before_ref'] = f"refs/bettergit/operations/{operation['id']}/before"
            operation['target_ref'] = f"refs/bettergit/operations/{operation['id']}/target"
            self.store.save(operation)
            self.checkpoint('plan_intent', operation)
            # One native ref transaction: both recovery refs appear or neither does.
            commands = (f"start\ncreate {operation['before_ref']} {head}\n"
                        f"create {operation['target_ref']} {target}\nprepare\ncommit\n")
            rc, _, _ = self.probe(['update-ref', '--stdin'], timeout=15, input_data=commands)
            if rc:
                raise RuntimeError('The recovery reference transaction failed; the plan is not ready and merge stopped.')
            self.checkpoint('backups_ready', operation)
            operation['state'] = ready
            self.store.save(operation)
            self.checkpoint('plan_ready', operation)
        return operation

    def describe(self, operation):
        self.say(f"Plan {operation['id']}: {operation['branch']!r} ← {operation['upstream']!r}")
        self.say(f"Commits: {operation['before'][:12]} -> merge {operation['target'][:12]}")
        self.say(f"Local branch: {operation['ahead']} ahead, {operation['behind']} behind.")
        push = operation.get('push_target')
        if push:
            self.say(f"Default push location for this branch: {push['remote']!r} {push['ref']!r} (informational; no push will run).")
        else:
            self.say('The default push target is unknown; upstream is not assumed to be the push target.')
        if operation['mergeability'] == 'conflicted':
            self.say('The preview found conflicts; worktree and index are unchanged. Conflicted files:')
            for path in operation['conflicts']:
                self.say('  ' + repr(path))
        else:
            self.say({'up_to_date': 'No merge is needed.', 'fast_forward': 'A fast-forward merge is available.',
                      'clean_merge': 'The preview merged cleanly; applying will create a merge commit.'}[operation['mergeability']])
        self.say(f"Files differing between the two commits: {len(operation['changed_files'])}; a clean preview does not establish application correctness.")
        for path in operation['changed_files'][:20]:
            self.say('  ' + repr(path))
        if len(operation['changed_files']) > 20:
            self.say('Use sync --plan --json or history --json to see the remaining files.')

    def validate_snapshot(self, operation, snapshot):
        if operation['root'] != self.root or operation['gitdir'] != self.gitdir or operation['common'] != self.common:
            raise RuntimeError('This plan belongs to another worktree; apply it there.')
        if operation['state'] != 'planned' or operation['mergeability'] not in ('fast_forward', 'clean_merge'):
            raise RuntimeError('This plan is not executable; inspect history or create a new plan.')
        if snapshot.state.head != operation['before'] or snapshot.state.branch_ref != operation['branch_ref']:
            raise RuntimeError('The branch/HEAD has changed; create a new plan.')
        if snapshot.context() != {key: operation[key] for key in ('branch_ref', 'branch', 'remote', 'upstream', 'push_target', 'config_digest')}:
            raise RuntimeError('Branch configuration has changed; create a new plan.')
        if snapshot.refs.get(operation['upstream']) != operation['target']:
            raise RuntimeError('The upstream has changed; create a new plan.')
        self.require_clean(snapshot.state)
        for name in ('before', 'target'):
            if snapshot.refs.get(operation.get(name + '_ref')) != operation[name]:
                raise RuntimeError('Recovery references are missing or changed; merge stopped.')

    def validate(self, operation, repreview=True):
        self.validate_snapshot(operation, RepositorySnapshot.read(self))
        if repreview and operation['ahead']:
            preview = self.preview(operation['before'], operation['target'], operation['ahead'], operation['behind'])
            if preview != {key: operation.get(key) for key in preview}:
                raise RuntimeError('The preview result has changed; create a new plan.')
            # Drivers can change mutable state; acquire a new snapshot afterwards.
            self.validate_snapshot(operation, RepositorySnapshot.read(self))
        self.protect_new_paths(operation['before'], operation.get('preview_tree', operation['target']))

    def apply(self, operation):
        # Validate the persisted plan before asking, but only rerun potentially
        # expensive drivers once, after confirmation. All mutable state is fresh.
        self.validate(operation, repreview=False)
        if not self.confirm('Apply this merge plan?' + (' This will create a merge commit.' if operation['ahead'] else '')):
            return 0
        self.validate(operation)
        operation['state'] = 'running'
        operation['started_at'] = timestamp()
        self.store.save(operation)
        self.checkpoint('before_merge', operation)
        try:
            rc = self.run_git(['merge', '--no-edit', '--no-overwrite-ignore',
                               '--no-ff' if operation['ahead'] else '--ff-only', operation['target']])
        except BaseException:
            operation['state'] = 'interrupted'
            self.store.save(operation)
            raise
        self.checkpoint('after_merge', operation)
        self.finish(operation, rc)
        return rc or (0 if operation['state'] == 'succeeded' else 1)

    def result_matches(self, operation, state):
        if state.branch_ref != operation['branch_ref'] or state.active:
            return False
        if not operation['ahead']:
            return state.head == operation['target']
        proof = self.get(['show', '--no-patch', '--no-show-signature', '--no-notes', '--format=%H%x00%T%x00%P', state.head]).split('\0')
        return (len(proof) == 3 and proof[0] == state.head and proof[1] == operation['preview_tree']
                and proof[2].split() == [operation['before'], operation['target']])

    def finish(self, operation, rc):
        state = worktree(self)
        operation['exit_code'] = rc
        operation['observed_head'] = state.head
        operation['post_state_clean'] = not state.dirty
        verified = self.result_matches(operation, state) and not state.dirty
        operation['state'] = 'succeeded' if rc == 0 and verified else 'needs_review' if rc == 0 else 'failed'
        if rc == 0:
            operation['after'] = state.head
        self.store.save(operation)
        self.checkpoint('result_saved', operation)
        if rc == 0:
            operation['after_ref'] = self.backup(operation['id'], 'after', state.head)
        self.checkpoint('after_ref_ready', operation)
        self.store.save(operation)
        self.checkpoint('final_saved', operation)
        self.say(f"Operation {operation['id']}: {operation['state']}. Use history / reconcile / recover to inspect the evidence.")
        if operation['state'] == 'needs_review':
            self.say('Git returned success, but the result or worktree differs from the plan; inspect hooks and concurrent changes.')

    def reconcile(self, operation_id):
        """Read-only evidence; never repair refs, reset, or mark logs successful."""
        operation = self.store.load(operation_id)
        result = {'id': operation_id, 'recorded_state': operation['state'], 'read_only': True,
                  'classification': 'unknown', 'evidence': {}, 'suggested_actions': []}
        if operation['root'] != self.root or operation['gitdir'] != self.gitdir:
            result['classification'] = 'other_worktree'
            return result
        snapshot = RepositorySnapshot.read(self)
        state = snapshot.state
        evidence = result['evidence']
        evidence.update(head=state.head, branch_ref=state.branch_ref, dirty=state.dirty, active=list(state.active))
        expected_refs = {name: f'refs/bettergit/operations/{operation_id}/{name}' for name in ('before', 'target', 'after')}
        evidence['backup_refs'] = {name: snapshot.refs.get(ref) for name, ref in expected_refs.items()}
        locks = [str(p.name) for p in (Path(self.gitdir) / 'index.lock', Path(self.common) / (operation['branch_ref'] + '.lock')) if p.exists()]
        if locks:
            result['classification'] = 'git_busy'
            evidence['locks'] = locks
        elif state.branch_ref != operation['branch_ref']:
            result['classification'] = 'branch_changed'
        elif any(evidence['backup_refs'][name] not in (None, operation[name]) for name in ('before', 'target')):
            result['classification'] = 'backup_mismatch'
        elif any(evidence['backup_refs'][name] is None for name in ('before', 'target')):
            result['classification'] = 'preparation_incomplete' if operation['state'] == 'preparing' else 'backup_missing'
        elif state.active:
            result['classification'] = 'matching_unfinished_merge' if self.owns_merge(operation) else 'unrelated_operation'
            if result['classification'] == 'matching_unfinished_merge':
                result['suggested_actions'] = [f'sync --continue {operation_id}', f'sync --abort {operation_id}']
        elif state.head == operation['before']:
            stale = (snapshot.config_digest != operation['config_digest'] or snapshot.refs.get(operation['upstream']) != operation['target'])
            result['classification'] = ('branch_changed' if operation['state'] in ('succeeded', 'needs_review') else
                                        'prepared_unrecorded' if operation['state'] == 'preparing' else
                                        'not_applied_or_interrupted' if operation['state'] in ('running', 'interrupted', 'failed') else
                                        'worktree_changed' if state.dirty else
                                        'plan_stale' if stale else 'not_applied')
        elif self.result_matches(operation, state):
            result['classification'] = ('needs_review' if state.dirty else 'completed'
                                        if operation['state'] == 'succeeded' and operation.get('after_ref')
                                        and evidence['backup_refs']['after'] == state.head else 'completed_unrecorded')
        else:
            result['classification'] = 'branch_changed'
        result['suggested_actions'].append(f'recover {operation_id} --preview')
        return result

    def owns_merge(self, operation):
        # Native operations outside this wrapper are never treated as ours based only on state.
        return (operation['root'] == self.root and operation['gitdir'] == self.gitdir
                and operation['state'] in ('running', 'failed', 'interrupted')
                and self.get(['symbolic-ref', '--quiet', 'HEAD']) == operation['branch_ref']
                and self.get(['rev-parse', 'HEAD']) == operation['before']
                and self.marker('MERGE_HEAD').exists()
                and self.marker('MERGE_HEAD').read_text().removesuffix('\n') == operation['target']
                and self.get(['rev-parse', 'ORIG_HEAD']) == operation['before'])

    def resume(self, operation_id, abort=False):
        operation = self.store.load(operation_id)
        if not self.owns_merge(operation):
            raise RuntimeError('No matching unfinished merge exists for this record; other Git operations will not be taken over.')
        question = 'Abort this merge? Git will try to restore the previous state; conflict-resolution edits may be lost.' if abort else 'Continue committing the resolved merge?'
        if not self.confirm(question):
            return 0
        if not self.owns_merge(operation):
            raise RuntimeError('The Git operation changed while waiting for confirmation; stopped.')
        if abort:
            rc = self.run_git(['merge', '--abort'])
            operation['state'] = 'aborted' if rc == 0 else 'failed'
            operation['exit_code'] = rc
            self.store.save(operation)
            return rc
        operation['state'] = 'running'
        self.store.save(operation)
        try:
            rc = self.run_git(['merge', '--continue'])
        except BaseException:
            operation['state'] = 'interrupted'
            self.store.save(operation)
            raise
        self.finish(operation, rc)
        return rc or (0 if operation['state'] == 'succeeded' else 1)

    def recover(self, operation_id, preview=True):
        operation = self.store.load(operation_id)
        before = operation.get('before')
        ref = operation.get('before_ref')
        if not ref or self.get(['rev-parse', ref]) != before:
            raise RuntimeError('No verifiable pre-operation recovery reference exists; recovery stopped.')
        branch = 'bettergit/recover/' + operation_id
        # A new ref preserves current staged, unstaged, untracked and conflict resolution work.
        summary = {'id': operation_id, 'state': operation['state'], 'before': before,
                   'after': operation.get('after', operation.get('observed_head')),
                   'recovery_branch': branch, 'changes_current_worktree': False,
                   'changed_files': operation.get('changed_files', [])}
        if preview:
            return summary
        self.say(f'Create recovery branch {branch!r} at {before[:12]}; the current worktree will be preserved.')
        if self.confirm('Create the recovery branch?'):
            if self.get(['rev-parse', ref]) != before:
                raise RuntimeError('The recovery reference changed while waiting for confirmation; stopped.')
            self.get(['update-ref', '-m', 'bettergit recover', 'refs/heads/' + branch, before, '0' * len(before)])
            operation['recovery_branch'] = branch
            self.store.save(operation)
            summary['created'] = True
        return summary
