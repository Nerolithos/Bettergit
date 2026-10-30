# A small history-to-recovery workflow

Use `history`, then `history ID`, then `recover ID --worktree ../new-checkout`. This extends two existing commands instead of introducing a separate worktree command tree. Long IDs can be copied from history; no ambiguous latest-record inference is used across linked worktrees.

The detail view shows recorded and observed state separately. It can inspect a record from another worktree without taking over that worktree's unfinished merge. Recovery uses the shared verified pre-operation reference, so an unrelated dirty current checkout can stay in place. It checks out a fixed commit in detached HEAD, which also avoids branch-already-checked-out conflicts.

## Why prioritize this

Community discussions describe difficulty tracking old worktrees and recovering work after deletion; these are examples of problems, not a demand survey or adoption evidence. See the [discussion on tracking many worktrees](https://www.reddit.com/r/codex/comments/1u7i1xo/how_do_you_manage_many_codexcreated_git_worktrees/) and [Git discussion about accidentally deleted local commits](https://www.reddit.com/r/git/comments/1pla6tp/accidentally_deleted_a_local_git_branch_with/).

Git's [worktree documentation](https://git-scm.com/docs/git-worktree) distinguishes detached checkouts, locked entries, prunable registrations, and repair. Bettergit uses those native mechanisms and leaves cleanup explicit. A prunable flag does not authorize deletion; a locked checkout may be on a temporarily disconnected drive.

## Boundaries

Only managed operation checkpoints are covered. Reflog searches, arbitrary file snapshots, universal undo, dependency copying, and automatic cleanup are not included. Checkout hooks and filters remain active; the workflow does not sandbox their effects. After failure, inspect the preserved directory and `git worktree list` before choosing a fresh destination. Recovery attempts retain recorded states; live registry flags are reported separately and do not verify file contents.

Creating a checkout does not change your current shell directory. Inspect the recovered files directly; if you make new commits in detached HEAD, create a native branch before removing the checkout. Use native `git worktree remove` only after preserving work you need.

## Validation

The isolated suite covers dirty staged/unstaged/untracked preservation, destination collisions, cancellation, checkpoint changes during confirmation, checkout failure, dirty hook results, missing checkpoints, cross-worktree recovery, registry removal/locking, newline paths, and the JSON history interface. These tests use temporary local repositories, not the user's working tree or terminal settings.
