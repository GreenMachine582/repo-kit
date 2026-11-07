---
name: worktree-start
description: Start new work on its own branch in its own worktree, with its own environment, so several Claude sessions can work on one repo without trampling each other. Use when picking up a task, starting a feature or fix, or when asked to "start a branch" or "new worktree". Wraps `rk wt start`.
---

# Start a worktree

One branch = one worktree = one Claude session. Never start work on a branch that another worktree has checked out.

## 1. Pick the branch name

`<type>/<short-description>`, with a Conventional Commits type, such as `feat/api-v2` or `fix/scan-resume`.

## 2. Start it

```bash
rk wt start <type/description>
```

Options:

- `--from <branch>`: branch off another feature branch, to build a chain of PRs. Without it, the branch comes off the
  profile's default base (`dev`).
- `--link <pkg>=<branch>`: use a local worktree of a GTH package instead of its pin. Repeatable.
- `--no-setup`: skip `[worktree].setup` (the venv and install). Only when asked.

`rk` creates the worktree under `<worktree_root>/<owner>-<repo>/<slug>-<hash6>`, runs the repo's setup, applies the
links, then prints the path and the command to start Claude in it.

## 3. Hand over

Tell the user the path and the printed command. Work for this branch happens in that worktree only. Open the PR as a
**draft**; mark it ready only when the work is done, since the PR bot only updates ready PRs.
