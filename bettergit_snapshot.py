"""Short-lived repository observations. Never cache mutable state across confirmation."""
from dataclasses import dataclass
import hashlib
import re

OID = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')
REF_FORMAT = '%(refname)%00%(objectname)%00%(upstream)%00%(upstream:remotename)%00%(push:remotename)%00%(push:remoteref)%00%(push)'


@dataclass(frozen=True)
class WorktreeState:
    head: str
    branch: str
    dirty: bool
    active: tuple

    @property
    def branch_ref(self):
        return 'refs/heads/' + self.branch if self.branch != '(detached)' else None


def worktree(engine):
    raw = engine.get(['status', '--porcelain=v2', '--branch', '-z', '--untracked-files=normal', '--ignore-submodules=none'])
    headers = {}
    dirty = False
    for field in raw.split('\0'):
        if not field:
            continue
        if not dirty and field.startswith('# '):
            # Header syntax is '# branch.<field> <value>'.
            key, _, value = field[2:].partition(' ')
            headers[key] = value
        else:
            dirty = True
    head, branch = headers.get('branch.oid', ''), headers.get('branch.head', '')
    if not OID.fullmatch(head) or not branch:
        raise RuntimeError('The current branch has no commit; create an initial commit first.')
    return WorktreeState(head, branch, dirty, tuple(engine.active()))


@dataclass(frozen=True)
class RepositorySnapshot:
    state: WorktreeState
    refs: dict
    rows: dict
    config: dict
    config_digest: str

    @classmethod
    def read(cls, engine):
        state = worktree(engine)
        raw_config = engine.get(['config', '--null', '--list'])
        config = {}
        for field in raw_config.split('\0'):
            if field:
                key, _, value = field.partition('\n')
                config.setdefault(key, []).append(value)
        # Avoid enumerating unrelated tag collections. Include custom fetch
        # namespaces and explicitly configured local upstream refs.
        patterns = {'refs/heads', 'refs/remotes', 'refs/bettergit'}
        for key, values in config.items():
            for value in values:
                if key.startswith('remote.') and key.endswith('.fetch') and ':' in value:
                    destination = value.partition(':')[2].split('*')[0].rstrip('/')
                    if destination.startswith('refs/'):
                        patterns.add(destination)
                elif key.startswith('branch.') and key.endswith('.merge') and value.startswith('refs/'):
                    patterns.add(value)
        raw = engine.get(['for-each-ref', '--format=' + REF_FORMAT, '--', *sorted(patterns)])
        rows = {}
        for line in raw.splitlines():
            fields = line.split('\0')
            if len(fields) != 7 or not OID.fullmatch(fields[1]):
                raise RuntimeError('Could not parse the complete reference snapshot.')
            rows[fields[0]] = fields
        refs = {name: row[1] for name, row in rows.items()}
        if state.branch_ref and refs.get(state.branch_ref) != state.head:
            raise RuntimeError('The branch/HEAD changed while collecting the snapshot; retry.')
        digest = hashlib.sha256(raw_config.encode('utf-8', errors='surrogateescape')).hexdigest()
        return cls(state, refs, rows, config, digest)

    def context(self):
        row = self.rows.get(self.state.branch_ref)
        if not row or not row[2] or not row[3]:
            raise RuntimeError('The current branch has no valid upstream; configure a tracking branch first.')
        remote, remote_ref, local_ref = row[4:7]
        if remote and not remote_ref and local_ref:
            candidates = set()
            if remote == '.' and local_ref.startswith('refs/heads/'):
                candidates.add(local_ref)
            for spec in self.config.get('remote.' + remote + '.fetch', []):
                src, sep, dst = spec.lstrip('+').partition(':')
                if not sep or src.startswith('^'):
                    continue
                if '*' not in dst and dst == local_ref:
                    candidates.add(src)
                elif src.count('*') == dst.count('*') == 1:
                    prefix, suffix = dst.split('*')
                    if local_ref.startswith(prefix) and local_ref.endswith(suffix):
                        middle = local_ref[len(prefix):len(local_ref)-len(suffix) if suffix else None]
                        candidates.add(src.replace('*', middle))
            if len(candidates) == 1:
                remote_ref = candidates.pop()
        push = {'remote': remote, 'ref': remote_ref} if remote and remote_ref else None
        return {'branch_ref': self.state.branch_ref, 'branch': self.state.branch, 'remote': row[3],
                'upstream': row[2], 'push_target': push, 'config_digest': self.config_digest}
