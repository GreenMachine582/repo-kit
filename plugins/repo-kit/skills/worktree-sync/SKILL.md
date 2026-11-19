---
name: worktree-sync
description: Bring the current worktree's branch up to date with its remote and its PR base by merging (never rebasing), resolve any conflicts, run the checks and push. Use when a PR is labelled needs-merge, when the PR bot's push was rejected, when the base moved, or when asked to "sync", "update the branch" or "merge dev in". Wraps `rk wt sync`.
---

# Sync a worktree

```bash
rk wt sync [branch]
```

`rk` must be on `PATH`. If it isn't, stop and give the user the install line from the repo-kit README; don't install
it yourself.

If `rk` refuses because it is older than the repo's pin ("this repo is pinned to X but this rk is Y"), stop and give
the user the install line it prints. Don't look for another `rk` on `PATH`.

With no branch, it syncs the current worktree's branch. It:

1. Refuses if there are uncommitted or untracked changes, or a merge is in progress. Commit them first (or ask the user what to do with them).
2. Merges `origin/<branch>` first, to pick up any merge the PR bot pushed. If the branch's upstream is gone and its
   PR is merged or closed, it stops: run `rk wt done` instead, not `wt sync`.
3. Merges `origin/<PR base>`, using the PR's live base, so chains follow a retargeted base. With no open PR yet, it
   uses the base the branch was started from.
4. On a conflict, stops and lists the conflicting files, leaving the merge in progress. Nothing is pushed.
5. Runs the repo's `[worktree].check`, pushes **without force**, and clears the `needs-merge` label. A rejected push
   (the branch moved again) says to run `rk wt sync` again, so do.

If the check fails and names a missing env dir (a worktree started with `--no-setup`), run the setup steps it prints,
then sync again.

## Resolving conflicts

When it stops on a conflict, resolve each listed file by keeping the intent of both sides, run the checks, commit the
merge, then run `rk wt sync` again to finish. If a resolution isn't clear from the code, ask the user rather than
guess.

## Never

- Rebase, or push with `--force` or `--force-with-lease`. Merge commits only.
- Resolve a conflict by dropping the other side's change without saying so.
