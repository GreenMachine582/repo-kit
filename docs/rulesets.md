[← Back to README](../README.md)

# 🔒 Rulesets and repo settings: `rk github`

Replaces `apply-rulesets.sh` and the per-repo `.github/rulesets/*.json` and `repo-settings.json`. Rulesets and
settings are **built in code** from the profile and config, then applied through the API. Nothing is copied into the
repo.

## What `dev-main` generates

Rulesets are built in `rulesets.py` and settings in `settings.py`, as Python dicts. Unit tests snapshot both.

| Item | Setting |
|---|---|
| Repo settings | Default branch `dev`; merge commits only (no squash, no rebase), titled "Merge pull request #N from …" with the PR title as the body; delete branch on merge; Update branch button; auto-merge allowed |
| `repo-kit: dev` ruleset | `refs/heads/dev`: no deletion, no force-push, a PR required (0 approvals, merge commit only), required checks |
| `repo-kit: main` ruleset | The same, on `refs/heads/main` |
| `repo-kit: tags` ruleset | `refs/tags/v*`: no deletion, update or creation, except by `[release].app` |

| Bypass | `dev` | `main` | `tags` |
|---|---|---|---|
| Admin role | Pull requests only: the logged emergency merge | Pull requests only | None: nobody moves or deletes a `v*` tag |
| `[release].app` (`[release].app_id`) | Always, when `[release].back_merge` is set: the back-merge push after a release ([checks-and-release.md](checks-and-release.md#releaseyml)) | None | Always: it creates the tags |

Required checks are `pr-checks / commits`, `pr-checks / pr-title` and `pr-checks / managed-files`, then
`[checks].required` ([checks-and-release.md](checks-and-release.md#check-names-and-rulesets-stay-in-step)). Each is
pinned to the GitHub Actions App (`integration_id` 15368), so a status posted by anything else doesn't count.

The App is named by its numeric ID, `[release].app_id`. A private App can't be looked up by name with your `gh`
login, so the ID lives in config, with gth-release-bot's as the default.

## Commands

| Command | Does |
|---|---|
| `rk github diff` (or `rk github`) | Compares the live repo settings and rulesets with what `.repo-kit.toml` describes. Read-only. Exits 1 if anything differs |
| `rk github apply` | Shows the same diff and asks. On yes it backs up the live settings and every ruleset, writes, and reads everything back. Needs an authenticated `gh` with admin rights. Never runs in CI. Exits 1 if anything still differs, or nothing was applied |
| `rk doctor` | Runs the same diff: WARN on drift |

Rulesets are matched by name (`repo-kit: dev`, `repo-kit: main`, `repo-kit: tags`). Rulesets with other names are
left alone and listed, so a GTH repo's old `dev`, `main` and `release tags` rulesets stay until you delete them.

Only the fields rk sets are compared, and list order never counts. Fields GitHub adds to what it returns (such as
`required_reviewers`, or `has_wiki` among the settings) are ignored. A rule type rk doesn't set is drift. Some
settings, such as `allow_update_branch`, are only returned to an admin: without admin rights they show as
`(not visible: needs admin)`.

```
$ rk github diff
repo settings: drifted
  default_branch:
    - "main"
    + "dev"
repo-kit: dev: drifted
  rule required_status_checks: checks:
    - test (ubuntu-latest) (app any)
    + pr-checks / commits (app 15368), pr-checks / managed-files (app 15368), pr-checks / pr-title (app 15368)
repo-kit: main: missing (would be created)
repo-kit: tags: matches
Left alone (not rk's): release tags
GitHub differs from .repo-kit.toml (- live, + wanted). `rk github apply` applies it.
```

### `rk github apply`

1. Refuses when `CI` or `GITHUB_ACTIONS` is set.
2. Prints the diff. If nothing differs, it stops there.
3. Asks `Apply these changes to <owner>/<repo>?`. Anything but yes, or no terminal to answer on, writes nothing.
4. Saves the live settings and every live ruleset, including the ones it leaves alone, to
   `.git/repo-kit/backup/<UTC time>/github.json`.
5. Patches only the settings that differ. Creates each missing `repo-kit: …` ruleset, and replaces each drifted
   one in place by its ID. Rulesets with other names are never touched.
6. Reads everything back. Anything still different is printed, and the exit code is 1.

It ends with the `Changed:` list: each setting group, ruleset and the backup folder. The default branch moves only
once it exists on origin (`rk install` creates it); until then that one change is left out, with a note. Running it
again when everything matches writes nothing.

## `rk branches prune`

Replaces `prune-branches.sh`, with the same rules. A dry run by default; `--apply` deletes. It never deletes:

- `dev` or `main`;
- the current branch;
- a branch with an open PR, or with no PR;
- a branch with commits beyond its merged PR.
