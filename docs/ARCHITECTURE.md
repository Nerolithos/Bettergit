# Architecture and behavioral boundaries

## Components

| Module | Responsibility |
| --- | --- |
| bettergit.py | CLI, confirmations, native command execution, slow-command observer |
| bettergit_publish.py | Confirmed, pinned-commit publication and remote verification |
| bettergit_snapshot.py | Short-lived worktree/configuration/reference observations |
| bettergit_workflow.py | Merge plans, durable records, backup refs, reconciliation, recovery branches |
| bettergit_diagnostics.py | Bounded local/remote diagnostics and Trace2 interpretation |
| bettergit_experiments.py | Paired untracked-cache measurements using temporary indexes |
| bettergit_pr.py | Read-only GitHub evidence collection and revision-aware PR explanations |
| bettergit_process.py | Time/output-limited probes and POSIX process-group cleanup |
| bettergit.zsh | Optional thin shell integration |

All core functionality uses Python's standard library and native Git. There is no LLM requirement or daemon.

## Managed merge lifecycle

A plan fixes the branch, starting commit, upstream commit, configuration digest, changed paths, and preview tree where applicable. Recovery references live under `refs/bettergit/operations/<ID>/`.

Preparation records `preparing`, creates before/target refs in one Git reference transaction, then saves the ready state. Applying obtains fresh state before confirmation and again afterwards. A divergent merge is re-previewed after confirmation, followed by another snapshot because custom drivers may have side effects.

The original operation begins only after checks pass. Git hooks/signing remain active. Final verification compares branch identity, expected parents/tree or exact fast-forward result, active operations, and worktree cleanliness. A successful Git exit with unexpected post-state is recorded as `needs_review`.

Records use private files, atomic replacement, and fsync. They do not form an atomic transaction with Git refs, the index, the filesystem, or external hook effects. Intermediate states are intentional.

## Reconciliation and concurrency

`reconcile` is observational. It does not rewrite logs, repair refs, delete locks, reset the worktree, or automatically continue another operation.

The Bettergit file lock serializes its own managed modifications across linked worktrees. Native Git and editors do not obey this lock. Snapshots detect some races; Git's own locking provides the final checks. There is no claim of atomic repository-wide isolation.

A dead wrapper does not imply its native Git child is dead. A running/failed/interrupted record with no definite completion evidence remains ambiguous, including when staged changes exist before MERGE_HEAD has been written.

Recovery creates a new branch at a verified saved commit. It preserves the current worktree, but cannot reconstruct arbitrary uncommitted/ignored contents or undo external side effects.

## Diagnostic and experiment isolation

Normal observation preserves original command I/O and exit semantics. Probes have bounded time and streaming output; 124 denotes timeout and 125 output overflow. Cleanup covers descendants remaining in the probe's process group, not programs deliberately starting a new session.

Raw traces, command arguments, remote URLs, and credential errors are not persisted in diagnostic reports. Classification uses fixed categories and executable positions. Confidence labels are heuristic categories, not calibrated probabilities.

The untracked-cache experiment uses Git's [alternate index mechanism](https://git-scm.com/docs/git) and per-command configuration. Each arm receives a separate copy of the original index. Two warmups per arm precede alternating measured pairs.

From the first discovery probe, fsmonitor is disabled. Measured status commands ignore submodules, disable split-index output, and use an empty hooks directory. Repositories with split indexes or external clean/process filters are refused rather than running them implicitly.

Original-index snapshots/hash checks run in killable helpers with a 64 MiB input cap. After measurement, the original index and configuration are checked again. A concurrent source update or inconsistent status output invalidates the comparison; the experiment never restores the original over another client's changes.

The experiment does not control the OS cache or prove filesystem suitability for untracked caching. A recommendation is a candidate for further verification, not an automatically applied fix.

## Validation

The isolated runner creates private HOME/ZDOTDIR/configuration directories and only local Git remotes. Deliberate transport tests use a fake SSH executable that creates no socket. English locale defaults apply only inside the test environment.

Coverage includes lifecycle SIGKILL, native Git overlap, preservation of staged/unstaged/untracked data, ignored path collisions, Unicode/newline paths, reference transactions, diagnostic negative controls, and budget/output enforcement.

A configured CI matrix covers Ubuntu/macOS and Python 3.10/3.12. Local results are evidence for the local environment only; remote CI jobs must run before being reported as passing.

## PR evidence boundary

The optional GitHub CLI adapter has a shared time budget, per-request timeout, output cap, and pagination limits. REST calls explicitly use GET; the narrowly allowed `gh pr view` query supplies server review decisions. No mutation commands are exposed. Reports omit raw API errors and credentials.

Rules, required checks, status contexts, reviews, and the final PR observation retain completeness/provenance markers. Test-merge checks take precedence when observed. Unknown policies or collection gaps cannot establish readiness. A final reread detects some concurrent updates; GitHub responses are not an atomic snapshot, and this tool never authorizes a merge.

## Diagnostic coverage and interpretation

Doctor adds `coverage.complete`, `coverage.incomplete_probes`, and `assessment.state`. Completion describes attempted probes, not exhaustive root-cause coverage. Failed/skipped observations cannot establish the absence of a bottleneck. Known remote errors map to fixed evidence/action text without persisting stderr; classification remains heuristic.

Status probes use Git's no-optional-locks flag, disable fsmonitor before discovery, and ignore submodule contents. A probe-policy identifier invalidates older baseline comparisons. Tests exercise a real executable fsmonitor helper, verify it was not invoked, and compare the original index byte-for-byte.

## Explicit publication

`sync --push` completes the managed merge workflow before publishing. It requires a clean worktree, a completed/current plan, a matching upstream/default push branch, one push URL equal to the fetch URL, and non-mirror configuration. Triangular destinations are deliberately rejected. Push confirmation can be skipped with `--yes`; merge confirmation cannot.

The push uses an explicit commit ID and one branch refspec, no force, no tag following, and no recursive submodule pushes. Existing hooks and server policies remain active. Native server checks reject incompatible concurrent updates. A read-only remote query verifies the published commit; successful native push alone does not establish a verified result. Records preserve publication states (`running_or_interrupted`, `unverified`, `verified`). Interrupted or failed publication must be inspected and retried, never automatically rolled back.

Publication and merge are separate operations: a failed push leaves local merge commits in place. This is not a distributed transaction. GitHub branch protection may require a PR instead of a direct push.
