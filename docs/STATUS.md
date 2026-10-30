# Project status

Assessment date: 2026-10-30.

**Bettergit is an early tool with a tested core for diagnostics and managed merges.** Its current focus is explaining Git waits and making merge-based synchronization reviewable and recoverable. A separate read-only GitHub PR explanation layer is now available.

## What it can help with today

| Situation | Available workflow | Current boundary |
| --- | --- | --- |
| Git appears stuck | Trace2 stage evidence; local measurements; optional remote read probe | Evidence is heuristic; unknown remains a valid result |
| Considering a performance setting | Paired untracked-cache experiment with disposable indexes | No permanent write-back; split indexes and external filters excluded |
| Local branch and upstream diverged | Fetch, immutable merge plan, conflict preview, revalidation, confirmation | Merge/fast-forward only; no managed rebase or push |
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
| 4. Worktree environment lifecycle | Not implemented as a user-facing feature |
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
