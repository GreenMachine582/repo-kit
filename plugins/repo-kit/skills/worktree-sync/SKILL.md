---
name: worktree-sync
description: Bring the current worktree's branch up to date with its remote and its PR base by merging (never rebasing), resolve any conflicts, run the checks and push. Use when a PR is labelled needs-merge, when the PR bot's push was rejected, when the base moved, or when asked to "sync", "update the branch" or "merge dev in". Wraps `rk wt sync`.
---

# Sync a worktree

```bash
rk wt sync [branch]
```

With no branch, it syncs the current worktree's branch. It:

1. Refuses if there are uncommitted or untracked changes. Commit them first (or ask the user what to do with them).
2. Merges `origin/<branch>` first, to pick up any merge the PR bot pushed.
3. Merges `origin/<PR base>`, using the PR's live base, so chains follow a retargeted base. With no open PR yet, it
   uses the base the branch was started from.
4. On a conflict, stops and lists the conflicting files, leaving the merge in progress. Nothing is pushed.
5. Runs the repo's `[worktree].check`, pushes **without force**, and clears the `needs-merge` label.

## Resolving conflicts

When it stops on a conflict, resolve each listed file by keeping the intent of both sides, run the checks, commit the
merge, then run `rk wt sync` again to finish. If a resolution isn't clear from the code, ask the user rather than
guess.

## Never

- Rebase, or push with `--force` or `--force-with-lease`. Merge commits only.
- Resolve a conflict by dropping the other side's change without saying so.
