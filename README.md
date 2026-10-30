# Bettergit

**Understand the wait. Preview the merge. Keep a way back.**

Bettergit helps you handle the uncertain parts of everyday Git: a command that seems stuck, a branch that has drifted from its upstream, or an interrupted operation whose outcome is unclear.

Keep using Git in your terminal. Add evidence when it gets slow, review a merge before applying it, and keep recovery references for the operations Bettergit manages.

## Why use it?

- **Find out what Git is waiting for.** Identify active file scans, remote transport, credential helpers, hooks, editors, and signing processes. Unknown causes stay unknown.
- **Review before you merge.** Fetch and preview with Git's own merge engine. See the commits and conflicted files before changing your worktree.
- **Check what happened after an interruption.** Durable records and recovery references let you compare a saved plan with the actual repository state.
- **Measure an optimization before adopting it.** Compare untracked-cache settings using disposable indexes, with no persistent configuration changes.
- **Stay in control.** Merges require confirmation. No automatic force-push, stash, rebase, or generated conflict resolution.

No API key, model service, Python packages, or background daemon required. CLI messages and test diagnostics are in English.

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

Local diagnostics compare tracked-only and untracked-file scans, measure process startup, and sample directory structure. Network probes are opt-in; normal diagnostics do not contact your remote.

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

For a single interactive flow:

```zsh
bettergit sync
```

To inspect only the cached upstream:

```zsh
bettergit check
```

Clean previews establish mergeability, not application correctness: run your project's tests after merging.

## When a command was interrupted

```zsh
bettergit history
bettergit reconcile <ID> --json
```

`reconcile` compares the record with commits, references, worktree state, and Git operation markers. It can distinguish incomplete preparation, a matching unfinished merge, a completed result with incomplete bookkeeping, or a branch changed by another process. It reports evidence without resetting the repository or silently rewriting the record.

For a matching unfinished merge:

```zsh
bettergit sync --continue <ID>
bettergit sync --abort <ID>
```

Both ask for confirmation. Aborting can discard conflict-resolution edits.

To keep an accessible reference to the original commit:

```zsh
bettergit recover <ID> --preview
bettergit recover <ID> --create-branch
```

Recovery creates a new branch at the saved commit and leaves your current worktree in place. It is not a universal undo or a backup of uncommitted files.

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
| `sync --plan` | Fetch and save a merge preview |
| `sync --apply <ID>` | Revalidate and confirm a saved merge plan |
| `check` | Inspect cached upstream state |
| `pr explain <PR> --repo OWNER/REPO` | Explain checks, reviews, and merge blockers |
| `history` | Find recorded operations |
| `reconcile <ID>` | Inspect the actual state after an interruption |
| `recover <ID> --create-branch` | Create a branch at a saved pre-operation commit |

`doctor`, `sync --plan`, `history`, `reconcile`, and `recover` support JSON output. `sync --json` requires `--plan`.

## Fits around Git

Use `command git ...` to bypass the wrapper, or set `BETTERGIT_ENABLED=0` to disable it in the current shell.

Only plain, argument-free `git pull` uses Bettergit's merge flow. Commands such as `git pull --rebase`, commands with global Git options, and aliases retain native Git behavior and receive slow-command monitoring, not managed-operation recovery. Plain pull uses a merge strategy rather than automatically following `pull.rebase`.

Existing hooks and signing configuration remain active during managed merges. Previewing may run configured merge drivers. Neither previews nor hooks are a sandbox for untrusted repositories.

## Current status

**An early tool with a tested core for diagnostics and managed merges.** It is useful today for developers who want clearer evidence and reviewable synchronization without moving to a different Git interface.

The core includes repository snapshots, atomic backup-reference creation, durable operation records, bounded subprocesses, ignored-file collision checks, and real process-termination tests. Local reference measurements reduced direct Git launches from **69 to 20** for fast-forward synchronization and **74 to 30** for a clean divergent merge; these are scenario-specific counts, not a universal speed claim.

Read-only PR merge-blocker explanations are now available through an optional GitHub CLI adapter. Managed worktree environments, rebase previews, and file-level undo remain on the roadmap. Windows is not currently supported. See [project status and priorities](docs/STATUS.md) for the fuller assessment.

## Test without touching your terminal setup

```sh
python3 scripts/test_isolated.py --measure
```

Tests use temporary HOME/ZDOTDIR directories, private Git configuration, local test remotes, and an independent Zsh process. Test messages and Git diagnostics use an English locale. Synthetic error cases and a fake SSH helper cover transport behavior without contacting an external server.

The runner verifies protected user configuration files remain unchanged. It also measures workflow overhead and evaluates labelled diagnostic fixtures; small fixture-set results are not a real-world accuracy claim.

For architecture and measured results, see [architecture and behavioral boundaries](docs/ARCHITECTURE.md). Earlier research and audit notes remain available under [docs](docs/).
