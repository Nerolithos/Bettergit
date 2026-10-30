# Explain a pull request with evidence

`bettergit pr explain 42 --repo OWNER/REPO` uses your authenticated GitHub CLI. A full HTTPS pull-request URL also supports enterprise hosts. Use `--budget 20` to bound collection and `--json` for structured output. No checkout is required.

The report answers which required checks are missing, pending, or failing; which review gates remain; and whether GitHub reports conflicts, an outdated branch, or a merge queue requirement. It separates observed approval counts from GitHub's eligibility decision. A green report is an observation, not permission to merge.

GitHub distinguishes checks on the head and test-merge commits, and requires both a check and a status when they share a required name. See [GitHub's required-check troubleshooting](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks). Bettergit preserves those distinctions and required application identities.

## Captured evidence

Run `bettergit pr explain --from-json examples/pr-missing-check.json` for an offline example. Captures are caller-supplied evidence and are not authenticated audit records. Schema version 1 contains `repository`, `pull_request`, `branch`, `rules`, `protection`, `checks`, `statuses`, `reviews`, `review_summary`, and `observed_again`. Checks and statuses are keyed by commit SHA. Each collected source has `status`, `complete`, and `data`, with a fixed `reason` on failures. The example is the reference shape for replay.

A successful explanation exits 0 for `ready`, `blocked`, `unknown`, or `not_open`. Invalid input or an unsuccessful initial collection exits 2. Scripts must inspect `outcome`, not treat exit 0 as merge approval.

## Deliberate limits

Collection defaults to a shared 20-second budget, at most four seconds per request, five pages of 100 entries per source, and a 4 MiB response limit. Hitting a limit preserves uncertainty. A final PR reread checks revision and state identity, but cannot make separate API responses atomic.

Missing branch-protection permissions are not evidence of no protection. [Classic branch-protection access](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection) and [active branch rules](https://docs.github.com/en/rest/repos/rules#get-rules-for-a-branch) are collected separately. Unsupported policies remain unknown. Individual review threads and all possible ruleset requirements are not modeled. No checks are rerun, reviews submitted, or PRs merged.

Tests cover replay and the subprocess adapter with a fake GitHub CLI, including partial pagination, revision changes, permission failures, and redaction. Live authenticated GitHub validation remains outstanding. The adapter uses [GitHub CLI PR view fields](https://cli.github.com/manual/gh_pr_view) and read-only REST endpoints; it adds no runtime Python dependencies.
