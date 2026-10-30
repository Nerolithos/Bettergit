# Project status

Assessment date: 2026-10-30.

**Bettergit is an early tool with a tested core for diagnostics and managed merges.** Its current focus is explaining Git waits and making merge-based synchronization reviewable and recoverable. A separate read-only GitHub PR explanation layer is now available.

## What it can help with today

| Situation | Available workflow | Current boundary |
| --- | --- | --- |
| Git appears stuck | Trace2 stage evidence; local measurements; optional remote read probe | Evidence is heuristic; unknown remains a valid result |
| Considering a performance setting | Paired untracked-cache experiment with disposable indexes | No permanent write-back; split indexes and external filters excluded |
| Local branch and upstream diverged | Fetch, immutable merge plan, conflict preview, revalidation, confirmation | Merge/fast-forward plus explicit sync --push to a matching tracking branch; no managed rebase |
| A managed operation was interrupted | Durable records, backup refs, read-only reconciliation, matching merge continue/abort | No global transaction lock against native Git clients |
| Want the previous commit accessible | Confirmed creation of a recovery branch | No snapshot of arbitrary uncommitted or ignored file contents |
| Starting a repository | Optional project-aware .gitignore after simple git init | No full project scaffolding or GitHub repository creation |

## Depth of the implementation

The substantial engineering is in repository state and process control:

- Short-lived snapshots combine branch/worktree state, configuration, and references. Mutable state is collected again after confirmation and after merge drivers run.
- before/target recovery references are created in one native Git transaction; preparing/running/final records preserve boundaries that span multiple systems.
- Reconciliation verifies actual commits, expected parents/trees, reference evidence, operation markers, and worktree state without resetting anything.
- Tests use real SIGKILL at eight durable lifecycle boundaries for both fast-forward and divergent merges, plus native Git concurrency scenarios.
- Subprocess probes enforce shared time budgets and streaming output limits, including process-group cleanup.
- Diagnostic classifiers have negative controls and controlled real Git waiting scenarios. Fixture performance is reported separately from field accuracy.
- The untracked-cache experiment uses private index copies, paired order, warmups, output-consistency checks, and source-index/configuration verification. It never restores over a concurrent native index update.

Local reference workflows directly launch Git 20 times for a fast-forward plan/apply and 30 times for a clean divergent merge, down from 69/74 before the snapshot refactor. A regression test enforces invocation budgets. Timings remain workload- and environment-specific.

## Current maturity and value

Useful for terminal users who regularly encounter confusing Git waits, diverged tracking branches, or interrupted merge operations. The immediate benefit is clearer evidence, explicit execution plans, and a preserved reference to the starting commit.

The strongest current differentiation is the connection between diagnosis, measurement, a reviewable merge plan, and post-interruption inspection. This still needs validation through routine use by people outside the project. Public issue research establishes problem examples, not adoption, willingness to pay, or product-market fit.

The implementation has meaningful reliability mechanisms, but it is not a mature general-purpose Git client. It lacks a packaged release/install flow, broad field evidence, a full supported-version test record, live GitHub validation, and comprehensive file-level recovery.

## Progress against the five proposed areas

| Area | Status |
| --- | --- |
| 1. Evidence-based Git diagnostics | Implemented core; paired untracked-cache experiment added; field coverage remains small |
| 2. Previewable, recoverable synchronization | Implemented and deepened for merge/fast-forward; rebase/refspec-aware push remain future work |
| 3. Explain why a PR cannot merge | Initial read-only implementation: revision-aware checks, rules, reviews, and explicit unknown evidence |
| 4. Worktree environment lifecycle | Detached checkpoint recovery available through recover; general lifecycle automation remains deferred |
| 5. Operation history and recovery | Implemented for managed merge operations; general file snapshots/undo not implemented |

## Previous core iteration

- Rewrote the README around practical workflows and user benefits with a portable quick start.
- Converted CLI help, confirmations, diagnostics, errors, and test expectations to English. The isolated runner forces an English Git locale; Unicode filenames remain covered as input data.
- Added the temporary-index untracked-cache experiment and its preservation/concurrency tests.
- Added overlapping native-Git tests: a force-updated tracking ref during a real merge driver, and a live native merge surviving the wrapper's death.
- Added an Ubuntu/macOS × Python 3.10/3.12 CI configuration. It has not been dispatched from this local workspace; configuration is not proof those matrix jobs pass.

Previous core validation: **88 tests passed** on macOS/Python 3.12.4/Git 2.45.2, including the new experiment and native-overlap cases. Test logs contain English messages and protected user configuration fingerprints match before/after. Workflow invocation budgets remain 20/30 for the reference cases. Full evidence is recorded in [release-review-results.json](release-review-results.json). Historical engineering measurements remain in [OPTIMIZATION.md](OPTIMIZATION.md).

## Recommended continuation

1. Run the CI matrix on the actual repository, add supported Git-version evidence, and resolve portability failures before calling the project cross-platform.
2. Recruit a small set of daily Git users, collect opt-in reproducible failures, and measure time saved, misleading diagnoses, and confirmation friction. Do not add telemetry by default.
3. Extend experiments cautiously: support split indexes and LFS/filter environments through fuller isolation, then investigate filesystem-aware optimizations. A promising timing ratio alone should not trigger configuration changes.
4. Validate the new `pr explain` adapter against opt-in live repositories, then expand policy coverage and actionable explanations for unresolved review threads. Offline fixtures and a fake executable do not establish live API compatibility.
5. Reuse the operation model for worktree setup/retry/cleanup. Add file-level snapshots and rebase only after their recovery boundaries and tests are explicit.

Keep the Git merge and diagnostic core small enough to inspect. Expansion should follow demonstrated user problems and verified state transitions.

## Bettergit rename and PR explanation iteration

The project and checkout are now named Bettergit. The lowercase `bettergit` command remains unchanged. PR explanation adds bounded, read-only GitHub CLI collection, revision consistency checks, paginated evidence, required-check application identity, server review decisions, and conservative handling of missing permissions or unsupported rules. It does not add automatic PR mutations.

The PR-specific suite passes 43 tests using captured evidence and a fake GitHub CLI executable. Live GitHub access has not been tested in this workspace; GitHub CLI is not installed. See [PR explanation boundaries](PR_EXPLAIN.md) and the latest validation record in [pr-review-results.json](pr-review-results.json).

Latest full isolated validation: **131 tests passed** on macOS, Python 3.12.5, Git 2.45.2, and Zsh 5.9. All six validation stages passed; protected user configuration and private configuration were unchanged. Logs use English. Reference Git invocation counts remain 20/30. The suite includes 43 PR tests; authenticated GitHub validation and remote CI remain outstanding.

## Diagnostic clarity iteration

Doctor now separates findings, findings with incomplete evidence, incomplete runs, and inconclusive runs. Reports name failed/skipped probes and distinguish known remote error signatures with targeted next steps. Timeout is not proof of a network fault. Status probes disable fsmonitor from discovery onward, ignore submodules, and prohibit optional index writes. Probe policy is part of baseline compatibility. See [diagnostic interpretation](DIAGNOSTICS.md).

The user supplied a GitHub screenshot showing all four previous-revision jobs passing: macOS/Ubuntu with Python 3.10/3.12. This is user-provided evidence, not independent verification of the latest local edits. New remote CI results remain pending.

Latest diagnostic iteration validation: **137 tests passed**, including six new diagnostic test methods, on macOS/Python 3.12.5/Git 2.45.2/Zsh 5.9. All isolated stages passed, configuration fingerprints remained unchanged, English logs contained no CJK text, and workflow invocation counts remained 20/30. Network error cases are controlled fixtures rather than live network validation. See [diagnostic-clarity-results.json](diagnostic-clarity-results.json).

## Explicit sync publication

Added `sync --push` for repositories that already have a matching upstream and push destination. The workflow fetches, previews/asks before merging when needed, confirms publication, pushes a pinned commit without force, and verifies the remote branch. Dirty worktrees stop without automatic commits. Separate push URLs, mirror remotes, and triangular destinations require native Git review.

Local full validation for sync publication: **150 tests passed**, including 13 publication tests. All six isolated stages passed; protected user configuration was unchanged. See [sync-review-results.json](sync-review-results.json).

Live GitHub test succeeded on 2026-10-30: `sync --push --yes` published `db3393cce8d976f21c8355be57c06333d5c9aaca` to `Nerolithos/Bettergit`, branch `main`, and verified the remote commit. This live case was local-ahead; divergent merges, cancellation, stale plans, hooks, and races were tested against local remotes.

## Unified history and recovery iteration

No top-level commands were added. `history <ID>` now gives a useful detail view combining saved state, fresh reconciliation, checkpoint availability, historical push state, and recovery checkout locations. `recover <ID> --worktree PATH` creates a verified detached checkpoint checkout while preserving today's worktree. It supports recovery from another linked worktree, refuses existing/nested destinations, rechecks after confirmation, and preserves failed checkout evidence. It is not a backup of arbitrary uncommitted files.

This deliberately avoids a second worktree-management CLI. General worktree lifecycle, environment setup, and automatic cleanup remain deferred. See [workflow rationale and boundaries](RECOVERY.md).

Latest recovery validation: **164 tests passed**, including 14 new recovery/history tests. All six isolated validation stages passed, protected user configuration was unchanged, and English logs contain no CJK text. A real-project history inspection exposed and verified a fix for ahead-only sync records after later commits: reconciliation no longer assumes a merge preview tree exists. Recovery checkout tests used temporary local repositories. See [recovery-review-results.json](recovery-review-results.json).

## CI evidence iteration

The existing four-environment workflow now preserves stage evidence, publishes deterministic English summaries, enforces existing invocation budgets and diagnostic fixture expectations, and saves per-matrix artifacts for 14 days. Timeout/launch failures retain status and logs. Official Action v7 references are SHA-pinned; grouped weekly Dependabot updates maintain them. No public commands or AI accounts were added.

Local validation: **174 tests passed**, including ten CI evidence tests. A deliberately failing temporary test suite verified that failure evidence survives and later stages still run. A real timed-out subprocess test verified same-group child termination. All seven validation stages and configuration fingerprints passed. Workflow syntax passed actionlint 1.7.12 (official archive SHA256 verified); shellcheck/pyflakes integrations were disabled for that static check. Remote execution is verified separately from local results. See [CI design](CI.md) and [ci-review-results.json](ci-review-results.json).

## Automatic sync behavior change

At the user's explicit request, plain `sync` now automatically stages all tracked changes and non-ignored new files, makes a snapshot commit with `v0.0.N` and a UTC timestamp, integrates clean remote changes, publishes without Bettergit confirmation, and verifies the remote commit. Existing `--push` spellings are compatible. Earlier iteration notes about dirty-worktree refusal and confirmation apply to reviewed/manual modes, not this new default. Real conflicts, unfinished Git operations, invalid destinations, and unexpected hook/concurrent changes stop the pipeline. No force-push or automatic conflict generation was added.

Automatic sync validation: **185 tests passed**, including 11 new automatic-flow tests. All seven validation stages passed; protected user configuration was unchanged. Tests cover non-interactive default CLI, add-all from subdirectories, deletions/ignore rules, snapshot version/timestamp, empty retries, clean divergence, conflict preservation, failed commit hooks, unfinished operations, and invalid destinations. See [autosync-review-results.json](autosync-review-results.json).
