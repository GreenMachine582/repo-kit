[← Back to README](../README.md)

# ✅ Checks and release

Two reusable workflows. Each consumer holds only a generated caller pinned to `@v<version>`.

## `pr-checks.yml`

Replaces GTH's `commits` check. Three jobs:

| Job | Checks |
|---|---|
| `commits` | Every commit in the PR is conventional, with a header of at most `commit_header_max`. Merge commits (including bot merges and **Update branch**) are skipped |
| `pr-title` | `not-conventional`: the title must not parse as a conventional commit (stops duplicate changelog lines with merge commits). `conventional`: the title must parse (for squash repos). Release PRs (`release: …`, `chore(main): release …`) are allowed |
| `managed-files` | `rk sync --check` passes |

Every repo gets this caller, managed by `rk sync` (`.github/workflows/pr-checks.yml`):

```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened, edited]   # edited: a title change re-runs pr-title
jobs:
  pr-checks:
    uses: GreenMachine582/repo-kit/.github/workflows/pr-checks.yml@v1.4.0
    with:
      ref: v1.4.0
```

A newer push cancels the checks still running for the same PR. The reusable workflow holds no secrets and only
reads (`contents: read`). Each job checks out the consumer and repo-kit at `ref`, and runs rk from source with
`python -P -m repo_kit`. The `-P` matters: without it Python looks in the consumer's checkout first, so a PR could add
its own `repo_kit/` folder and pass its own checks. `tests/unit/test_workflows.py` keeps it there.

| Job | Checks out | Runs |
|---|---|---|
| `commits` | The PR head, with every branch | `rk check commits origin/<base>..<head>`, where `<base>` is the PR's live base (`dev`, or the parent of a chained PR) |
| `pr-title` | The merge ref, for `[checks].pr_title` | `rk check pr-title "<title>"`. The title reaches the step through `env`, never inline |
| `managed-files` | The merge ref: what lands if the PR merges | `rk sync --check` |

| Input | Default | |
|---|---|---|
| `ref` | (required) | The repo-kit tag to run, the same as the `@v…` pin |
| `managed-files` | `true` | Set to `false` only by repo-kit's own caller (`pr-checks-self.yml`), until it adopts its own files |

On a failure, the job summary says how to fix it.

The rules are implemented once in `repo_kit/checks.py` and unit-tested, so the optional local `commit-msg` hook
(installed by `rk install --hooks`, running `rk check commit-msg`) and CI use the same code.

### The rules

- **Types:** `feat`, `fix`, `perf`, `build`, `docs`, `refactor`, `test`, `ci`, `chore`, `style`, `revert`. Branch
  names (`rk wt start`) use the same list.
- **Header:** `<type>[(<scope>)][!]: <description>`, in lower case, with one space after the colon and at most
  `[checks].commit_header_max` characters. Git's own `Revert "…"` subject isn't conventional: write `revert: …`.
- **PR titles:** `not-conventional` is matched case-insensitively, so `Feat: x` is refused too. `release: …` and
  `chore(main): release …` pass under either rule.
- **Messages:** the hook reads the first line Git will keep. Comment lines and everything below the `commit -v`
  scissors line are ignored. During a merge it passes, as CI skips merge commits.

### Commands

| Command | Does | Exit code |
|---|---|---|
| `rk check commits <range>` | Checks every non-merge commit in the range (`git log --no-merges`), and prints each problem with its short SHA | 1 if any commit fails |
| `rk check commit-msg <file>` | Checks one message file. Run by the hook | 1 to stop the commit |
| `rk check pr-title <title>` | Checks a title against `[checks].pr_title` | 1 if it breaks the rule |

### The `commit-msg` hook

`rk install --hooks` writes it where `git rev-parse --git-path hooks/commit-msg` points, so `core.hooksPath` and
every worktree of the clone are covered. It runs `rk check commit-msg`, and skips the check with a warning if `rk`
isn't on `PATH`. If the hook is already there and unchanged, nothing is written. If it's an older rk hook, it's backed up to
`.git/repo-kit/backup/` and replaced. Another tool's hook is never replaced: install stops before writing anything,
and says how to add the check to that hook by hand.

### Check names and rulesets stay in step

Checks from a reusable workflow report as `<caller job> / <called job>`, such as `pr-checks / commits`. Because rk
generates both the caller workflow and the rulesets, the required-check names always match. Required checks are
`pr-checks / commits`, `pr-checks / pr-title`, `pr-checks / managed-files`, plus whatever `[checks].required` lists
from the repo's own CI.

## `release.yml`

Wraps the release-please action. `dev-main` repos get this caller, managed by `rk sync`
(`.github/workflows/release.yml`):

```yaml
on:
  push:
    branches: [main]
jobs:
  release:
    uses: GreenMachine582/repo-kit/.github/workflows/release.yml@v1.4.0
    with:
      client-id: ${{ vars.GTH_RELEASE_BOT_CLIENT_ID }}
      target-branch: main
      back-merge-into: "dev"            # "" when [release].back_merge = false
    secrets:                            # resolves only in the gth-release-bot environment
      RELEASE_BOT_PRIVATE_KEY: ${{ secrets.GTH_RELEASE_BOT_PRIVATE_KEY }}
```

Both jobs that mint a token name the `gth-release-bot` environment, which only the release branch may use, so a
workflow on any other branch can't read the key ([github-app.md](github-app.md#where-the-keys-live)).

| Input | Default | |
|---|---|---|
| `client-id` | (required) | `gth-release-bot`'s Client ID. Empty fails the run with a pointer to [github-app.md](github-app.md#releases-gth-release-bot) |
| `target-branch` | (required) | The branch releases are cut from: the profile's release branch |
| `back-merge-into` | `""` | The branch to merge the release back into. Empty skips the back-merge |

It runs no rk code, so it checks out no repo-kit and needs no `ref`: the `@v…` pin alone fixes it. Its jobs hold only
`contents: read`. Every write goes through a `gth-release-bot` token: CI runs on the release PR, and only that App
may create `v*` tags. Runs in a repo queue one behind another and are never cancelled.

1. **`release-please`** mints the token and runs release-please on `target-branch`, with the consumer's
   `release-please-config.json` (managed) and `.release-please-manifest.json` (seeded, never overwritten, so adoption
   keeps the current version).
2. **`back-merge`**, only when a release was created and `back-merge-into` is set: merges `main` into `dev` with a
   merge commit, as `gth-release-bot[bot]`, through GitHub's merges API. GitHub makes and signs the commit, so it
   passes a ruleset that requires signed commits, and it merges into `dev` as it is at that moment, so a merge into
   `dev` at the same time can't race it. `gth-release-bot` bypasses the `dev` ruleset for this one merge
   ([rulesets.md](rulesets.md)).
   - If `dev` already contains `main`, nothing is merged.
   - On a conflict, or a refusal, the run fails and its summary says how to recover: open a PR from `main` to `dev`
     and merge it with a merge commit.

### `release-please-config.json` and the manifest

`dev-main` repos get both from `rk sync`:

- **`release-please-config.json`** is managed. Only `release-type` (`[project].release_type`), `package-name`
  (`[project].name`) and `extra-files` (`[project].extra_files`) vary, so the version-bump rules are identical in
  every repo. The text is the config GTH repos
  already use, so `rk sync` takes theirs over unchanged.
- **`.release-please-manifest.json`** is seeded: created once, with the version in `pyproject.toml`
  (`[project].version`) or `package.json`, else `0.0.0`, and release-please's from then on. A repo that already has
  one keeps it.

#### `extra_files`, and repo-kit's own pin

`[project].extra_files` ([configuration.md](configuration.md#projectextra_files)) lists files release-please bumps
in the release PR. It's built in code, not templated, so any path stays valid JSON. rk never writes a version
itself (hard rule: release-please owns them), it only tells release-please where else the version lives.

repo-kit needs this for itself. Its own `pr-checks-self.yml` runs the PR's own rk, whose version is the one
release-please just bumped, and `rk sync --check` requires the pin to equal the engine's version. So a release PR
that moves `pyproject.toml` to 0.3.0 while `.repo-kit.toml` still pins 0.2.0 fails `managed-files`. Two entries fix it:

```toml
extra_files = [
  { type = "toml", path = ".repo-kit.toml", jsonpath = "$.version" },
  { type = "json", path = ".repo-kit.lock", jsonpath = "$.version" },
]
```

The lock is listed too because it records the pin it was written at: `--check` calls a lock whose `version` differs
from the pin out of date. Nothing else in the check changes: the lock compares by content, so release-please
re-indenting the JSON doesn't matter, and the seeded `.repo-kit.toml` is never compared beyond its pin. The other
managed files render the same at the new version, because repo-kit's own callers are `unmanaged`
(`pr-checks.yml`, `update-prs.yml`, `release.yml`). `tests/scenarios/test_sync.py` covers this: moving both
versions passes, moving only the pin is drift.

`CHANGELOG.md` is never written by rk: release-please creates it on the first release.

| Commit | Effect |
|---|---|
| `feat` | Minor |
| `fix`, `perf`, `build`, `revert` | Patch |
| Breaking (`!` or `BREAKING CHANGE:`) | Major, or minor below 1.0 |
| `docs`, `test`, `refactor`, `ci`, `chore`, `style` | Hidden from the changelog |

A unit test keeps the changelog sections in step with the commit types in `checks.TYPES` and the table in the
CONTRIBUTING block.

Tags are `vX.Y.Z`, never moved, created only by `gth-release-bot`.
