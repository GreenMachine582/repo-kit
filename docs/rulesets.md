[← Back to README](../README.md)

# 🔒 Rulesets and repo settings: `rk github`

Replaces `apply-rulesets.sh` and the per-repo `.github/rulesets/*.json` and `repo-settings.json`. Rulesets and
settings are **built in code** from the profile and config, then applied through the API. Nothing is copied into the
repo.

## What `dev-main` generates

| Item | Setting |
|---|---|
| Repo settings | Default branch `dev`; merge commits only; delete branch on merge; Update branch button; auto-merge allowed |
| `repo-kit: dev` ruleset | PR required, merge commit only, required checks, no force-push or deletion |
| `repo-kit: main` ruleset | Same as `dev` |
| `repo-kit: tags` ruleset | `v*`: no update or deletion; creation only by `[release].app` |
| Bypass | Admin, pull-request bypass only (the logged emergency merge) |

Required checks are listed in [checks-and-release.md](checks-and-release.md#check-names-and-rulesets-stay-in-step).

## Commands

| Command | Does |
|---|---|
| `rk github diff` | Shows the difference between live GitHub state and what the config describes. Read-only, and the default |
| `rk github apply` | Applies after showing the diff and asking. Needs an authenticated `gh` with admin rights. Never run in CI |
| `rk doctor` | Runs the diff and reports drift |

Rulesets are matched by name (`repo-kit: dev`, `repo-kit: main`, `repo-kit: tags`). Rulesets with other names are
left alone and listed by `doctor`.

## `rk branches prune`

Replaces `prune-branches.sh`, with the same rules. A dry run by default; `--apply` deletes. It never deletes:

- `dev` or `main`;
- the current branch;
- a branch with an open PR, or with no PR;
- a branch with commits beyond its merged PR.
