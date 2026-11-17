[← Back to README](../README.md)

# 🌳 Worktrees

**One branch = one worktree = one Claude session**, each with its own environment, outside OneDrive. The plugin's
skills (`worktree-start`, `worktree-sync`, `worktree-done`) are thin wrappers around these commands; `rk wt list`
shows every worktree at a glance.

## 📁 Paths

```
<worktree_root>/<owner>-<repo>/<slug>-<hash6>
~/dev/wt/GreenMachine582-photo-ledger/feat-api-v2-1a2b3c
```

- `worktree_root` comes from the user config (default `~/dev/wt`).
- The slug is the branch name with unsafe characters replaced by `-`, cut to 40 characters.
- The hash is the first 6 hex characters of SHA-1 of the full branch name, so `feat/api-v2` and `feat/api/v2` can't
  collide.
- `<owner>-<repo>` comes from the `origin` URL.

Every `wt` command needs Git 2.38 or newer (checked on each run). Each one that writes ends with a `Changed:` list of
exactly what it did, even when it stops part-way. A refusal exits 1. Without a `.repo-kit.toml`, every config default applies.

`[worktree].setup` and `.check` strings are split like a POSIX shell would (no pipes or `&&`) and run in the worktree.
A command whose first word ends in `.sh` runs through `bash`: Git Bash on Windows, never the WSL `bash.exe`.

## ▶️ `rk wt start <type/desc> [--from <branch>] [--link <pkg>=<branch>] [--no-setup]`

1. Validate the branch name: `<type>/<description>`, with a Conventional Commits type from
   [CONTRIBUTING.md](../CONTRIBUTING.md).
2. Refuse if the branch already exists, locally or on `origin`.
3. Branch off `origin/<base>`, where the base is the profile's default (`dev`) or `--from` to chain on another branch.
   A `--from` branch that isn't pushed yet is used locally. The new branch tracks nothing, and its base is recorded
   in `git config branch.<name>.rkBase`.
4. Add the worktree.
5. Apply links (see below).
6. Run `[worktree].setup` (idempotent), unless `--no-setup`. If it fails, the worktree stays; fix the command and run
   it there by hand.
7. Print the path and the command to start Claude in it.

## 🔄 `rk wt sync [branch]`

Merge only. With no branch, it syncs the current worktree's branch.

1. Refuse on uncommitted or untracked changes, or a merge in progress. Untracked `[worktree].env_dirs` don't count.
2. Merge `origin/<branch>` first, to pick up any merge the PR bot pushed. If the branch had an upstream that is now
   gone and its PR is merged or closed (and contains the branch's head), stop: GitHub deleted it after the merge, and
   `wt sync` won't push it back. Run `rk wt done` instead.
3. Merge `origin/<base>`, where the base is the open PR's live base. With no open PR, it's the base recorded by
   `wt start`, then the profile's default. The output says which it used.
4. On a conflict, stop and list the files. The merge is left in progress for Claude to resolve and commit; then run
   `rk wt sync` again. Nothing is pushed.
5. Run `[worktree].check`. If it fails, nothing is pushed.
6. Push without force, if there's anything new. A rejected push (the branch moved again) says to run `wt sync` again.
7. Clear the conflict label (`needs-merge`) from the open PR.

This is also how a session catches up after losing a push race with the bot.

## ⏹️ `rk wt done [branch]`

Refuses unless every condition holds. Each has its own override:

| Condition | Override |
|---|---|
| The worktree belongs to this repo, under `<worktree_root>/<owner>-<repo>/` (not the main checkout) | None |
| It's run from outside that worktree | None |
| No `[worktree].env_dirs` entry holds files Git tracks (rk deletes env dirs) | None: edit `env_dirs` |
| The PR is merged or closed, and the branch's current head is in that PR (no PR found, or an older PR of a reused branch name, counts as unmerged) | `--allow-unmerged` |
| Nothing is uncommitted or untracked (`[worktree].env_dirs` excepted) | `--discard-changes` |
| Nothing is unpushed: every commit is on some remote branch, such as a merged PR's base | `--delete-unpushed` |
| No ignored files would be deleted (`.env`, local databases): outside `env_dirs`, tool caches (`__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `*.egg-info`) and rk's links file | None: move or delete them first |

Every failing condition is reported at once, each with its override. All checks run before anything is removed. Then it
undoes links, removes `[worktree].env_dirs` (the part most likely to hit a Windows file lock), removes the worktree,
deletes the local branch, and prunes. The skill text forbids Claude from using any override unasked.

If removal stops partway (a locked file), rk says which path to free, and rerunning `rk wt done` finishes the job
without `--discard-changes`: tracked files the first run already deleted are expected, while any other change still
refuses. A worktree whose folder is already gone is just pruned and its branch deleted.

`[worktree].env_dirs` entries must be plain relative folder names (`.venv`, `build/cache`): no `.` or `..`, absolute
path, `*`/`?`/`[`/`:` or `.git`, or loading `.repo-kit.toml` fails, because each entry is both excluded from the dirty
check and deleted.

## 📋 `rk wt list`

Read-only: writes nothing and doesn't fetch, so remote branches are as of the last fetch. Always exits 0. One aligned
line per worktree of this repo under `<worktree_root>/<owner>-<repo>/` (the main checkout isn't listed):

```
BRANCH       PR         LOCAL                      PATH
feat/api-v2  #12 open   clean                      ~/dev/wt/GreenMachine582-photo-ledger/feat-api-v2-1a2b3c
fix/typo     #9 merged  1 uncommitted, 2 unpushed  ~/dev/wt/GreenMachine582-photo-ledger/fix-typo-4d5e6f
```

- **PR** is the branch's PR (the open one, else the most recent) and its state: `open`, `draft`, `merged`, `closed`,
  or `none`. When origin isn't on GitHub, or `gh` fails, it shows `?` and a note at the end says why the PRs are
  unknown; the worktrees are still listed.
- **LOCAL** counts uncommitted or untracked changes (`[worktree].env_dirs` excepted) and unpushed commits (on no
  remote branch), the same tests `wt done` applies. `clean` means neither; `directory missing` means Git still lists
  the worktree but its folder is gone.

A worktree whose PR is merged or closed is ready for `rk wt done`; `rk doctor` reports those as stale.

## 🔗 Local GTH mode

A worktree can run against local worktrees of the GTH packages instead of their pins, with
`rk wt start --link greentechhub-core=feat/x`. The linked worktree must already exist at
`<worktree_root>/<owner>-greentechhub-core/<slug>-<hash6>` (start it in that repo first), or nothing is created.
The link is written to `.repo-kit.local.toml`, which is added to the repo's `info/exclude` if Git doesn't already
ignore it. `local_gth.py` (which stays in greentechhub-core) resolves each package
in this order:

1. An explicit path from the worktree's gitignored `.repo-kit.local.toml`, written by `rk wt start --link`.
2. `GTH_<PKG>_PATH`.
3. `GTH_ROOT/<pkg>`.
4. The sibling of the **main** checkout, found through `git rev-parse --git-common-dir`.

The consumer-side wrapper `scripts/use-local-gth.sh` is identical in every GTH consumer. Making it a managed file,
from an `extras/use-local-gth` template enabled by `[extras].use_local_gth`, is a Future item
([TODO.md](../TODO.md#-future)); until then each GTH repo keeps its own copy, and `--link` works without it.

CI and production never use local GTH mode, and `wt done` undoes the links.
