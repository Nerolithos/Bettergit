# Bettergit

**Understand the wait. Preview the merge. Keep a way back.**

Bettergit helps you handle the uncertain parts of everyday Git: a command that seems stuck, a branch that has drifted from its upstream, or an interrupted operation whose outcome is unclear.

Keep using Git in your terminal. Add evidence when it gets slow, review a merge before applying it, and keep recovery references for the operations Bettergit manages.

Bettergit integrates with GitHub through the authenticated GitHub CLI (`gh`). Its read-only `pr explain` workflow uses the GitHub REST and GraphQL APIs to inspect pull requests, required checks, branch rules, and review decisions, then explain merge blockers or missing evidence. This integration is under active development; live inspection requires GitHub authentication.

## Why use it?

- **Find out what Git is waiting for.** Identify active file scans, remote transport, credential helpers, hooks, editors, and signing processes. Unknown causes stay unknown.
- **Save and synchronize in one command.** `sync` stages all non-ignored changes, commits a version and UTC timestamp, integrates clean remote updates, pushes, and verifies the remote commit.
- **Review before you merge.** Fetch and preview with Git's own merge engine. See the commits and conflicted files before changing your worktree.
- **Check what happened after an interruption.** Durable records and recovery references let you compare a saved plan with the actual repository state.
- **Measure an optimization before adopting it.** Compare untracked-cache settings using disposable indexes, with no persistent configuration changes.
- **Stop on real conflicts.** Automatic sync applies clean previews only. No force-push, automatic stash/rebase, or generated conflict resolution. Saved-plan application and recovery still require confirmation.

No AI API key, model service, Python packages, or background daemon required. CLI messages and test diagnostics are in English.

## Get started

Requires **Python 3.10+**, **Git 2.38+**, and a POSIX environment. The optional shell integration requires Zsh. Local validation currently runs on macOS; a Linux/macOS CI matrix is included.

From your downloaded checkout:

```zsh
cd /path/to/Bettergit
source "$PWD/bettergit.zsh"
```

This loads Bettergit into **this shell only**. To keep it, add an absolute `source /path/to/Bettergit/bettergit.zsh` line to your own `.zshrc`.

Prefer an explicit command without wrapping Git? Use Python directly:

```sh
python3 /path/to/Bettergit/bettergit.py doctor
python3 /path/to/Bettergit/bettergit.py sync --plan
python3 /path/to/Bettergit/bettergit.py run -- status
```

If you used the old smart-edit prototype, remove its startup entry and restart Zsh before migrating. Bettergit does not rewrite spelling or replace your editing key bindings. Existing `git` aliases/functions may need adjustment.

## When a Git command feels stuck

With the shell integration loaded, run Git normally:

```zsh
export BETTERGIT_SLOW_SECONDS=5
git fetch
```

After the threshold, Bettergit reads Git's execution trace while the command keeps running. A slow hook and a waiting credential helper call for different next steps; a transport wait alone does not establish a network outage.

For a closer look:

```zsh
bettergit doctor
bettergit doctor --budget 15 --samples 3 --json
bettergit doctor --network --remote origin --budget 20
```

Reports distinguish findings, findings with gaps, incomplete evidence, and inconclusive runs. Failed or skipped probes are listed explicitly. Network failures have separate DNS, proxy, SSH host-key, authentication, TLS, connection, and repository-access explanations with specific next steps. A timeout alone never establishes a network outage.

Local diagnostics compare tracked-only and untracked-file scans, measure process startup, and sample directory structure. Network probes are opt-in; normal diagnostics do not contact your remote. Status probes disable fsmonitor, skip submodule contents, and avoid optional index writes; their timing excludes those costs. See [diagnostic interpretation](docs/DIAGNOSTICS.md).

### Test a performance setting without committing to it

```zsh
bettergit doctor --experiment untracked-cache --budget 20 --samples 3
```

The experiment warms two disposable indexes, alternates cache-on/cache-off measurements, and checks that the original index and configuration remain unchanged. It reports the measured result rather than applying a setting for you.

Split indexes and external clean/process filters are currently excluded. Submodules and fsmonitor are disabled in both experimental arms. The OS filesystem cache is not controlled, so repeat promising results before changing configuration.

You can also track comparable diagnostic runs:

```zsh
bettergit doctor --save-baseline
bettergit doctor --compare
```

Comparisons require matching versions, platform, and sampling settings.

## When your branch and upstream have diverged

```zsh
bettergit sync --plan
```

Bettergit fetches your configured upstream, pins the commits, checks for local changes, and previews the merge. Conflicts are reported by filename without entering a conflicted merge in your worktree.

Review the plan, then apply its ID:

```zsh
bettergit sync --apply <ID>
```

Before applying, it rechecks your branch, upstream, configuration, worktree, and recovery references. It asks for confirmation and checks again after your answer. A stale plan is rejected.

For automatic staging, committing, merging, and publishing:

```zsh
bettergit sync
```

To inspect only the cached upstream:

```zsh
bettergit check
```

Clean previews establish mergeability, not application correctness: run your project's tests after merging.

## One command to save and synchronize

In an existing repository with a configured upstream and working GitHub credentials:

```zsh
bettergit sync
```

From any subdirectory, Bettergit automatically:

1. Stages all repository changes: modifications, deletions, and non-ignored new files (`git add --all`).
2. Creates a commit such as `Bettergit sync v0.0.42 | 2026-10-30T06:20:00Z`.
3. Fetches the upstream and previews incoming changes with Git's merge engine.
4. Applies a fast-forward or clean merge without asking, then pushes one branch without force.
5. Reads the remote branch to verify the resulting commit.

Live-tested on this repository on 2026-10-30: plain `sync` staged changes, created `Bettergit sync v0.0.6 | 2026-10-30T05:29:58Z` (`6f3076f`), pushed to GitHub, and verified the remote commit.

No confirmation is required in this flow. `sync --push` and `sync --push --yes` remain compatibility spellings for the same automatic flow. Without shell integration, use `python3 /path/to/Bettergit/bettergit.py sync`.

**Snapshot versions:** `v0.0.N` uses the local branch's first-parent commit count plus one. It labels a snapshot, not a semantic release; it does not edit version files or create tags. Shallow or rewritten histories can repeat labels. The timestamp is UTC. No changes means no empty commit, while existing local commits can still be pushed.

Git ignore rules remain active, and ignored files are never forced into the index. Existing staged changes are included together with other changes. Hooks, signing, authentication, and server branch rules remain active and may require prior setup or reject the operation.

If the merge preview finds conflicts, sync reports the files and stops before entering a conflicted merge or pushing. The newly created local snapshot commit is preserved. Fetch, commit, and push failures also preserve local work for inspection/retry; synchronization is not an atomic transaction. Concurrent edits or unexpected hook changes stop publication. An unfinished Git operation must be resolved explicitly first.

### Initial setup and reviewed workflows

`git init` alone does not configure GitHub. Check `git remote -v` and `git branch -vv`. If the remote branch already exists, configure tracking explicitly, for example:

```sh
git fetch origin
git branch --set-upstream-to=origin/main
```

Use your actual branch name. An initial commit, a matching tracked remote branch, one push URL equal to the fetch URL, and working author identity/authentication are required. New empty remotes, unrelated histories, mirror remotes, and triangular fork destinations are not automatically configured.

For a review-first workflow, commit your changes yourself, use `sync --plan`, then `sync --apply <ID>` after inspection. These modes do not automatically stage or snapshot your files and still require confirmation for applying a merge. The `git pull` wrapper also retains its reviewed merge-only behavior; it never automatically stages, commits, or pushes.

## Inspect history and recover without disturbing today's work

Start with one history flow:

```zsh
bettergit history
bettergit history <ID>
```

The detail view combines the saved operation with a fresh repository inspection: what was recorded, what Git shows now, whether the checkpoint still exists, and which action fits the state. It also shows recorded publication results and recovery checkout locations. Publication results are historical; history does not contact GitHub.

To inspect the version before a managed operation in a separate directory:

```zsh
bettergit recover <ID> --worktree ../recovery-checkout
```

Review the destination and confirm. Bettergit creates a detached worktree at the verified checkpoint, leaving your current branch, staged changes, and files in place. The directory must be new and outside existing worktrees. It verifies the resulting commit, repository, detached state, and cleanliness; unexpected checkout changes are marked `needs_review`.

The recovery contains **committed checkpoint contents**, not a backup of uncommitted, ignored, or untracked files. Existing checkout hooks and filters may run and are not sandboxed. Nothing is automatically deleted, even after failure.

Use `bettergit history <ID>` again to find the checkout and inspect its registry/lock/prunable flags. These flags come from Git's worktree registry; they do not establish that every file is healthy or that cleanup is safe. Keep any new work before using native Git to remove it.

For a matching unfinished merge, history suggests `sync --continue <ID>` or `sync --abort <ID>`. Both ask for confirmation; aborting can discard conflict-resolution edits. For advanced use, `reconcile <ID> --json` remains available, and `recover <ID> --create-branch` creates only a recovery branch. History details and recovery also support JSON output.

## When a pull request looks green but cannot merge

```zsh
bettergit pr explain 42 --repo OWNER/REPO
bettergit pr explain https://github.com/OWNER/REPO/pull/42 --json
```

See missing required checks, failed or pending checks, review gates, merge conflicts, and merge-queue requirements in one read-only report. Bettergit distinguishes the PR head from GitHub's test-merge revision and reads the PR again to detect changes during collection. Missing permissions, incomplete pages, and unsupported policies produce `unknown`, never a claim that merging is safe.

Live inspection requires the optional [GitHub CLI](https://cli.github.com/) and your existing authentication. It does not require a local checkout and does not merge, approve, rerun checks, or change repository settings.

Try a captured example without GitHub access:

```zsh
bettergit pr explain --from-json examples/pr-missing-check.json
```

A successful inspection exits zero even when the PR is blocked or evidence is unknown; inspect the JSON `outcome` when scripting. This is an explanation tool, not a CI approval gate. See [evidence and limitations](docs/PR_EXPLAIN.md).

## Start a repository with useful ignore rules

```zsh
git init my-project
```

After a supported `init`, Bettergit can offer a `.gitignore` for detected Python, Node, Rust, or Go projects. It previews the template and asks before creating it. Existing files are preserved.

## Commands at a glance

| Command | Use it to |
| --- | --- |
| `doctor` | Investigate local Git slowness |
| `doctor --network` | Opt into a bounded read-only remote probe |
| `doctor --experiment untracked-cache` | Compare a setting using temporary indexes |
| `sync` | Automatically stage, versioned-commit, integrate clean updates, push, and verify |
| `sync --plan` | Fetch and save a merge preview |
| `sync --apply <ID>` | Revalidate and confirm a saved merge plan |
| `check` | Inspect cached upstream state |
| `pr explain <PR> --repo OWNER/REPO` | Explain checks, reviews, and merge blockers |
| `history [ID]` | Find operations or inspect their current state and recovery actions |
| `reconcile <ID>` | Inspect the actual state after an interruption |
| `recover <ID> --worktree <PATH>` | Inspect a saved checkpoint in a separate checkout |

`doctor`, `sync --plan`, `history`, `reconcile`, and `recover` support JSON output. `sync --json` requires `--plan`.

## Fits around Git

Use `command git ...` to bypass the wrapper, or set `BETTERGIT_ENABLED=0` to disable it in the current shell.

Only plain, argument-free `git pull` uses Bettergit's merge flow. Commands such as `git pull --rebase`, commands with global Git options, and aliases retain native Git behavior and receive slow-command monitoring, not managed-operation recovery. Plain pull uses a merge strategy rather than automatically following `pull.rebase`.

Existing hooks and signing configuration remain active during managed merges. Previewing may run configured merge drivers. Neither previews nor hooks are a sandbox for untrusted repositories.

## Current status

**An early tool with a tested core for diagnostics and managed merges.** It is useful today for developers who want clearer evidence and reviewable synchronization without moving to a different Git interface.

The core includes repository snapshots, atomic backup-reference creation, durable operation records, bounded subprocesses, ignored-file collision checks, and real process-termination tests. Local reference measurements reduced direct Git launches from **69 to 20** for fast-forward synchronization and **74 to 30** for a clean divergent merge; these are scenario-specific counts, not a universal speed claim.

Read-only PR merge-blocker explanations are now available through an optional GitHub CLI adapter. Separate recovery worktrees are available through `recover`; general worktree environment automation, rebase previews, and file-level undo remain on the roadmap. Windows is not currently supported. See [project status and priorities](docs/STATUS.md) for the fuller assessment.

## Test without touching your terminal setup

```sh
python3 scripts/test_isolated.py --measure
```

Tests use temporary HOME/ZDOTDIR directories, private Git configuration, local test remotes, and an independent Zsh process. Test messages and Git diagnostics use an English locale. Synthetic error cases and a fake SSH helper cover transport behavior without contacting an external server.

Each CI matrix job publishes an English summary and retains stage logs plus JSON evidence as a downloadable artifact for 14 days. Failed and timed-out stages remain visible; incomplete evidence never becomes a pass. The same report can be generated locally with `python3 scripts/validation_report.py --input /tmp/bettergit-validation`.

CI gates the existing Git invocation budgets (25 fast-forward, 30 divergent) and labelled diagnostic expectations. Elapsed timings remain informational. Action references are pinned to verified commit SHAs and maintained by grouped weekly Dependabot updates. No AI account or additional Bettergit command is needed.

The runner verifies protected user configuration files remain unchanged. It also measures workflow overhead and evaluates labelled diagnostic fixtures; small fixture-set results are not a real-world accuracy claim.

For architecture and measured results, see [architecture and behavioral boundaries](docs/ARCHITECTURE.md). Earlier research and audit notes remain available under [docs](docs/).

## Support

For support, contact [Nerolithos@hotmail.com](mailto:Nerolithos@hotmail.com). Report bugs and request improvements through [GitHub Issues](https://github.com/Nerolithos/Bettergit/issues).
