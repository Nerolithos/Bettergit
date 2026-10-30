# Interpret a diagnostic report

Run `bettergit doctor --samples 3 --budget 15`; add `--json` for structured evidence. Remote reads require `--network`, with `--remote origin` when the branch has no upstream. No settings are automatically repaired.

## Read the result in order

1. `assessment.state`: `findings` means evidence suggests an issue; `findings_with_gaps` also has incomplete probes; `incomplete` has no established cause and missing measurements; `inconclusive` establishes no clear cause even though attempted probes completed. None is a certificate that the repository is healthy.
2. `coverage.incomplete_probes` names failed, timed-out, output-limited, or skipped probes. An incomplete scan cannot establish that scanning is fast.
3. Each finding includes evidence, interpretation, next step, and heuristic confidence. Confidence is not a calibrated probability.

Status comparisons alternate tracked-only and untracked scans. They disable fsmonitor from discovery onward, ignore submodules, and disable optional index writes. Consequently they do not measure a configured fsmonitor helper or submodule traversal. Baselines include the probe policy; older or different policies are incompatible. Git documents [background status refresh and untracked performance](https://git-scm.com/docs/git-status) and [optional locks](https://git-scm.com/docs/git).

## Network findings

| Code | Evidence and useful next step |
| --- | --- |
| `dns_failure` | Hostname resolution signature; inspect DNS and the remote hostname |
| `proxy_failure` | Proxy resolution, tunnel, or authentication signature; inspect proxy settings |
| `ssh_host_key_failure` | SSH identity verification failed; verify the fingerprint through a trusted source |
| `authentication_failure` | Credential or SSH identity rejection; inspect the selected identity and permissions |
| `tls_failure` | Certificate/handshake signature; inspect trust chain, clock, and trusted proxy |
| `connection_refused` | Connection establishment failed; inspect port, firewall, and service |
| `remote_repository_unavailable` | Missing repository or concealed access; verify path and account access |
| `network_timeout` | Time budget expired; waiting stage remains unknown |
| `remote_output_limit` | Output cap reached; reachability was not established |
| `remote_read_failed` | Unrecognized failure; inspect native Git output locally |

Classification matches known error signatures and is heuristic, especially with translated/custom helpers. Raw errors, remote URLs, and credentials are excluded from reports. Successful remote access describes one read attempt, not push permission or ongoing service health. Never disable TLS verification or blindly remove known_hosts entries based on this tool.

Trace2 observes active stages: a running transport may be waiting for authentication, a proxy, or the server. Completed spans overlap and are not percentages of total runtime. An unknown result is preferable to inventing a cause.

## Validation scope

Tests exercise distinct error signatures, unknown/output/time-limit cases, incomplete evidence, real fsmonitor non-execution, and source-index preservation. Existing controlled Git hook/editor/credential/transport waits and crash recovery remain in the full isolated suite. User-supplied CI evidence shows the previous revision passed Ubuntu/macOS with Python 3.10/3.12; that does not establish CI success for subsequent edits.
