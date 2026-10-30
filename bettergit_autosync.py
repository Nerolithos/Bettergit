"""The explicitly requested one-command add/commit/synchronize/push flow."""
from datetime import datetime, timezone
from bettergit_publish import publication_target
from bettergit_snapshot import RepositorySnapshot


def commit_all(engine):
    initial = RepositorySnapshot.read(engine)
    if initial.state.active:
        raise RuntimeError('An existing Git operation is in progress; finish or abort it before automatic sync.')
    context = initial.context()
    publication_target(engine, initial)
    # Explicit root makes subdirectory invocation include all repository changes.
    # Git's ignore rules remain authoritative; ignored files are never forced in.
    engine.say('Automatic sync: staging all tracked changes and non-ignored new files across the repository.')
    rc = engine.run_git(['-C', engine.root, 'add', '--all', '--', '.'])
    if rc:
        raise RuntimeError(f'Automatic staging failed (exit {rc}); no commit or push was attempted. Inspect the index before retrying.')
    current = RepositorySnapshot.read(engine)
    if current.context() != context or current.state.head != initial.state.head or current.state.active:
        raise RuntimeError('Branch, HEAD, configuration, or operation state changed during staging; staged files were preserved and sync stopped.')
    rc, _, _ = engine.probe(['diff', '--cached', '--quiet', '--exit-code'])
    if rc == 0:
        engine.say('No staged changes; no empty snapshot commit will be created.')
        return None
    if rc != 1:
        raise RuntimeError('Could not inspect staged changes; sync stopped without committing or pushing.')
    count = engine.get(['rev-list', '--count', '--first-parent', initial.state.head])
    if not count.isdigit():
        raise RuntimeError('Could not determine the snapshot version; staged files were preserved.')
    version = 'v0.0.' + str(int(count) + 1)
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    message = f'Bettergit sync {version} | {timestamp}'
    engine.say('Creating snapshot commit: ' + message)
    rc = engine.run_git(['commit', '-m', message])
    if rc:
        raise RuntimeError(f'Snapshot commit failed (exit {rc}); staged work was preserved. Check hooks, identity, or signing. No push was attempted.')
    after = RepositorySnapshot.read(engine)
    if after.context() != context or after.state.active or after.state.dirty:
        raise RuntimeError('Commit completed but branch/configuration or files changed; inspect the repository before retrying. No push was attempted.')
    parents = engine.get(['show', '--no-patch', '--no-show-signature', '--format=%P', after.state.head]).split()
    if after.state.head == initial.state.head or parents != [initial.state.head]:
        raise RuntimeError('The resulting commit does not match the expected history; no push was attempted.')
    return {'before': initial.state.head, 'commit': after.state.head,
            'version': version, 'timestamp': timestamp, 'message': message}
