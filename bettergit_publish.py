"""Publish a pinned commit to the verified tracking branch without force."""
from bettergit_snapshot import RepositorySnapshot


def publication_target(engine, snapshot):
    context = snapshot.context()
    # A triangular push is a different workflow: never guess its destination.
    target = context['push_target']
    merge_refs = snapshot.config.get('branch.' + context['branch'] + '.merge', [])
    if (not target or target['remote'] != context['remote'] or len(merge_refs) != 1
            or target['ref'] != merge_refs[0] or not target['ref'].startswith('refs/heads/')
            or target['remote'] == '.'):
        raise RuntimeError('Publish requires the default push target to match the tracked remote branch. Configure an upstream and a matching push destination first.')
    remote, ref = target['remote'], target['ref']
    if snapshot.config.get('remote.' + remote + '.mirror', ['false'])[-1].lower() not in ('false', 'no', 'off', '0'):
        raise RuntimeError('Mirror remotes are not supported by sync --push.')
    urls = engine.get(['remote', 'get-url', '--push', '--all', '--', remote]).splitlines()
    fetch_url = engine.get(['remote', 'get-url', '--', remote])
    if len(urls) != 1 or urls[0] != fetch_url:
        raise RuntimeError('Publish requires one push URL identical to the fetch URL; separate or multiple destinations need native Git review.')
    return remote, ref


def publish(engine, operation, yes=False):
    snapshot = RepositorySnapshot.read(engine)
    engine.require_clean(snapshot.state)
    context = snapshot.context()
    expected_head = operation.get('after') if operation['state'] == 'succeeded' else operation['before']
    if (operation['state'] not in ('up_to_date', 'succeeded')
            or operation['root'] != engine.root or operation['gitdir'] != engine.gitdir
            or snapshot.state.head != expected_head
            or context != {key: operation[key] for key in context}
            or snapshot.refs.get(context['upstream']) != operation['target']):
        raise RuntimeError('The synchronization plan was not completed or no longer matches this branch; no push was attempted.')
    remote, ref = publication_target(engine, snapshot)
    head = snapshot.state.head
    upstream = snapshot.refs.get(context['upstream'])
    if not upstream:
        raise RuntimeError('The tracking reference is missing; fetch and retry.')
    rc, _, _ = engine.probe(['merge-base', '--is-ancestor', upstream, head])
    if rc:
        raise RuntimeError('Local history does not contain the fetched upstream; no push was attempted.')
    engine.say(f"Publish {head[:12]} to {remote!r} {ref!r}; only this branch will be pushed, without force.")
    if not yes and not engine.confirm('Push this committed history to the remote?'):
        engine.say('Push skipped; local commits are preserved.')
        return 1
    current = RepositorySnapshot.read(engine)
    engine.require_clean(current.state)
    if (current.context() != context or current.state.head != head
            or current.refs.get(context['upstream']) != upstream):
        raise RuntimeError('Branch, configuration, or upstream changed before push; retry synchronization.')
    operation['publication'] = {'state': 'running_or_interrupted', 'commit': head,
                                'remote': remote, 'ref': ref}
    engine.store.save(operation)
    # Explicit SHA and refspec prevent hooks/concurrent branch changes from
    # silently changing the published commit. Native server checks reject races.
    rc = engine.run_git(['-c', 'push.followTags=false', 'push', '--porcelain',
                         '--no-force', '--no-follow-tags', '--recurse-submodules=no', '--', remote, head + ':' + ref])
    publication = operation['publication']
    publication['push_exit_code'] = rc
    if rc:
        publication['state'] = 'unverified'
        engine.store.save(operation)
        engine.say('Push did not complete successfully. Local commits remain. The remote may have changed; inspect access, branch rules, and native Git output, then rerun sync --push. No force-push was attempted.')
        return rc
    # Read the actual push endpoint, rather than trusting a local tracking ref.
    rc, out, _ = engine.probe(['ls-remote', '--refs', '--', remote, ref], timeout=15)
    rows = [row.split('\t') for row in out.splitlines()]
    verified = rc == 0 and rows == [[head, ref]]
    publication['state'] = 'verified' if verified else 'unverified'
    engine.store.save(operation)
    if not verified:
        engine.say('Git reported push success, but the remote commit could not be verified or has already changed. Run sync --push again to inspect current state.')
        return 1
    after = RepositorySnapshot.read(engine)
    if after.state.head != head or after.context() != context or after.state.dirty or after.state.active:
        engine.say('The pinned commit was verified remotely, but local state changed during push; inspect the worktree before continuing.')
        return 1
    engine.say(f'Synchronized: remote branch verified at {head[:12]}.')
    return 0
