"""Labelled event fixtures, deliberately including misleading URL/path arguments."""
CASES = [
    ('index_refresh', [{'event': 'region_enter', 'sid': '1', 'category': 'index', 'label': 'refresh'}], {'filesystem'}),
    ('ssh_transport', [{'event': 'child_start', 'sid': '1', 'child_id': 1, 'argv': ['ssh', 'git@example.invalid']}], {'transport'}),
    ('credential_helper', [{'event': 'child_start', 'sid': '1', 'child_id': 1, 'argv': ['git-credential-test', 'get']}], {'authentication'}),
    ('hook_wait', [{'event': 'child_start', 'sid': '1', 'child_id': 1, 'child_class': 'hook', 'argv': ['sleep', '20']}], {'hook'}),
    ('shell_editor', [{'event': 'child_start', 'sid': '1', 'child_id': 1, 'use_shell': True, 'argv': ['code --wait']}], {'interaction'}),
    ('signing_wait', [{'event': 'child_start', 'sid': '1', 'child_id': 1, 'argv': ['/usr/local/bin/gpg', '--sign']}], {'interaction'}),
    ('misleading_remote_name', [{'event': 'child_start', 'sid': '1', 'child_id': 1, 'argv': ['ssh', 'git@code-gpg-credential.invalid']}], {'transport'}),
    ('misleading_path_argument', [{'event': 'child_start', 'sid': '1', 'child_id': 1, 'argv': ['cat', '/tmp/vim-ssh-credential-secret']}], set()),
    ('finished_child', [{'event': 'child_start', 'sid': '1', 'child_id': 1, 'argv': ['ssh']}, {'event': 'child_exit', 'sid': '1', 'child_id': 1}], set()),
    ('finished_session', [{'event': 'child_start', 'sid': '1', 'child_id': 1, 'argv': ['ssh']}, {'event': 'exit', 'sid': '1'}], set()),
    ('no_evidence', [], set()),
]
