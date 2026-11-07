---
name: worktree-done
description: Remove a finished worktree, its environment and its local branch once the PR is merged or closed. Use when a PR has merged, or when asked to "clean up", "finish" or "remove the worktree". Wraps `rk wt done`, which refuses unsafe states.
---

# Finish a worktree

```bash
rk wt done [branch]
```

It refuses unless all of these hold:

- the worktree belongs to this repo;
- the branch's PR is merged or closed;
- nothing is uncommitted;
- nothing is unpushed.

Then it undoes any `--link`s, removes the `[worktree].env_dirs` (such as `.venv`) and the worktree, deletes the local
branch and prunes.

## Overrides: never unasked

Each refusal has its own override: `--allow-unmerged`, `--discard-changes`, `--delete-unpushed`. The last two lose
work. **Never pass any of them unless the user asked for that exact override in this conversation.** When `rk`
refuses, report the refusal and what it found, and ask.

Run it from outside the worktree being removed (for example from the main checkout).
