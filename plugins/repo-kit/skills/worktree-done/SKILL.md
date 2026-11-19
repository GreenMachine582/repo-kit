---
name: worktree-done
description: Remove a finished worktree, its environment and its local branch once the PR is merged or closed. Use when a PR has merged, or when asked to "clean up", "finish" or "remove the worktree". Wraps `rk wt done`, which refuses unsafe states.
---

# Finish a worktree

```bash
rk wt done [branch]
```

`rk` must be on `PATH`. If it isn't, stop and give the user the install line from the repo-kit README; don't install
it yourself.

If `rk` refuses because it is older than the repo's pin ("this repo is pinned to X but this rk is Y"), stop and give
the user the install line it prints. Don't look for another `rk` on `PATH`.

It refuses unless all of these hold:

- the worktree belongs to this repo;
- the branch's PR is merged or closed. With no PR, a branch with no commits of its own passes; one with commits
  counts as unmerged;
- nothing is uncommitted;
- nothing is unpushed;
- no `[worktree].env_dirs` entry holds files Git tracks;
- no ignored files would be deleted (`.env`, local databases), tool caches aside.

Every failing condition is reported at once.
Then it undoes any `--link`s, removes the `[worktree].env_dirs` (such as `.venv`) and the worktree, deletes the local
branch and prunes.

## Refusals with no override

Tracked files inside an `env_dirs` entry, and ignored files that would be deleted, can't be overridden. Report what
`rk` found and ask the user. Never move or delete those files unasked.

## Overrides: never unasked

Each refusal has its own override: `--allow-unmerged`, `--discard-changes`, `--delete-unpushed`. The last two lose
work. **Never pass any of them unless the user asked for that exact override in this conversation.** When `rk`
refuses, report the refusal and what it found, and ask.

Run it from outside the worktree being removed (for example from the main checkout).

If a removal stops partway because of a locked file, free the path `rk` names and run `rk wt done` again: it finishes
the job without `--discard-changes`.

To see which worktrees are finished, `rk wt list` shows each one's PR state and any uncommitted or unpushed
work. It changes nothing.
