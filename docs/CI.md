# CI evidence for existing guarantees

The existing Isolated tests workflow still runs Ubuntu/macOS with Python 3.10/3.12. No new public CLI commands, model credentials, or runtime dependencies are introduced.

## What is automated

- English job summaries list each stage, test count, failed test identifiers, configuration protection results, and reference Git invocation counts.
- Each matrix job uploads its own logs, summary, quality gates, and structured measurements with a 14-day retention period. Summary and upload steps use `always()` to run after ordinary failures. Hard cancellation, runner loss, or failure before checkout can still prevent evidence collection.
- Invocation budgets reuse existing test limits: fast-forward 25 and divergent merge 30. Current reference counts are 20 and 30. Wall-clock measurements are informational because hosted runner load varies.
- Labelled diagnostic cases must match their expected classes. This is regression coverage, not real-world diagnostic accuracy.
- Stages persist running/completed status atomically. Timeout or launch failure is recorded instead of aborting the evidence pipeline; later stages can still run. Process-group cleanup does not cover descendants deliberately starting independent sessions.
- Official checkout/setup-python/upload-artifact v7 references were verified against upstream tags and pinned to full SHAs. Dependabot checks weekly and groups Action updates into reviewable PRs; it does not auto-merge them.
- Obsolete runs for the same PR/ref are cancelled. `merge_group` runs the same checks if a merge queue is enabled; this configuration does not enable a queue.

## Read or reproduce a report

Run `python3 scripts/test_isolated.py --measure`, then `python3 scripts/validation_report.py --input /tmp/bettergit-validation`. Use the same output directory for both. The report exits nonzero on failed, missing, or incomplete evidence. On Actions it also appends the report to `GITHUB_STEP_SUMMARY`. Failed test names are extracted from the bounded log tail; full logs remain the authoritative evidence.

Reports do not query users' GitHub repositories, authorize merges/recovery, or replace local diagnosis. The artifact includes controlled test logs, not user credential files or worktree snapshots. The workflow keeps contents-read permissions and does not execute privileged `pull_request_target` code or write bot comments.

## Why no agent yet

The current outputs already have structured evidence. Deterministic summaries and regression gates are easier to verify than model-generated explanations and require no inference account. A future failure-analysis agent is justified only if real failures remain difficult to interpret after these reports. It must remain advisory.

References: [job summaries](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands#adding-a-job-summary), [artifacts](https://docs.github.com/en/actions/tutorials/store-and-share-data), [Dependabot Actions updates](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/auto-update-actions), [merge queue checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).
