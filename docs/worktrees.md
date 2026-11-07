[← Back to README](../README.md)

# 🌳 Worktrees

**One branch = one worktree = one Claude session**, each with its own environment, outside OneDrive. The plugin's
skills (`worktree-start`, `worktree-sync`, `worktree-done`) are thin wrappers around these commands.

## 📁 Paths

```
<worktree_root>/<owner>-<repo>/<slug>-<hash6>
~/dev/wt/GreenMachine582-photo-ledger/feat-api-v2-1a2b3c
```

- `worktree_root` comes from the user config (default `~/dev/wt`).
- The slug is the branch name with unsafe characters replaced by `-`, cut to 40 characters.
- The hash is the first 6 hex characters of SHA-1 of the full branch name, so `feat/api-v2` and `feat/api/v2` can't
  collide.

## ▶️ `rk wt start <type/desc> [--from <branch>] [--link <pkg>=<branch>] [--no-setup]`

1. Validate the branch name (`<type>/<description>`).
2. Branch off the profile's default base (`dev`), or `--from` to chain on another branch.
3. Add the worktree.
4. Run `[worktree].setup` (idempotent), unless `--no-setup`.
5. Apply links (see below).
6. Print the path and the command to start Claude in it.

## 🔄 `rk wt sync [branch]`

Merge only.

1. Refuse on uncommitted or untracked changes.
2. Merge `origin/<branch>` first, to pick up any merge the PR bot pushed.
3. Merge `origin/<PR base>`, using the PR's live base.
4. On a conflict, list the files for Claude to resolve.
5. Run `[worktree].check`.
6. Push without force.
7. Clear the conflict label (`needs-merge`).

This is also how a session catches up after losing a push race with the bot.

## ⏹️ `rk wt done [branch]`

Refuses unless every condition holds. Each has its own override:

| Condition | Override |
|---|---|
| The worktree belongs to this repo | None |
| The PR is merged or closed | `--allow-unmerged` |
| Nothing is uncommitted | `--discard-changes` |
| Nothing is unpushed | `--delete-unpushed` |

Then it undoes links, removes `[worktree].env_dirs` and the worktree, deletes the local branch, and prunes. The skill
text forbids Claude from using any override unasked.

## 🔗 Local GTH mode

A worktree can run against local worktrees of the GTH packages instead of their pins, with
`rk wt start --link greentechhub-core=feat/x`. `local_gth.py` (which stays in greentechhub-core) resolves each package
in this order:

1. An explicit path from the worktree's gitignored `.repo-kit.local.toml`, written by `rk wt start --link`.
2. `GTH_<PKG>_PATH`.
3. `GTH_ROOT/<pkg>`.
4. The sibling of the **main** checkout, found through `git rev-parse --git-common-dir`.

The consumer-side wrapper `scripts/use-local-gth.sh` is identical in every GTH consumer, so it is a managed file, from
the `extras/use-local-gth` template, enabled by `[extras].use_local_gth`.

CI and production never use local GTH mode, and `wt done` undoes the links.
