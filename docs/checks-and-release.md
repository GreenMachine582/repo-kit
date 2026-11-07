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

The rules are implemented once in `repo_kit/checks.py` and unit-tested, so the optional local `commit-msg` hook
(installed by `rk install --hooks`, running `rk check commit-msg`) and CI use the same code.

### Check names and rulesets stay in step

Checks from a reusable workflow report as `<caller job> / <called job>`, such as `pr-checks / commits`. Because rk
generates both the caller workflow and the rulesets, the required-check names always match. Required checks are
`pr-checks / commits`, `pr-checks / pr-title`, `pr-checks / managed-files`, plus whatever `[checks].required` lists
from the repo's own CI.

## `release.yml`

Wraps the release-please action:

1. Mint a `gth-release-bot` token, so CI runs on the release PR.
2. Run release-please with the consumer's `release-please-config.json` (managed) and `.release-please-manifest.json`
   (seeded, never overwritten, so adoption keeps the current version).
3. On release, with the `dev-main` profile and `back_merge = true`: merge `main` back into `dev` with a merge commit.

The config is rendered from `[project]`, so the version-bump rules are identical in every repo:

| Commit | Effect |
|---|---|
| `feat` | Minor |
| `fix`, `perf`, `build` | Patch |
| Breaking (`!` or `BREAKING CHANGE:`) | Major, or minor below 1.0 |
| `docs`, `test`, `refactor`, `ci`, `chore`, `style` | Hidden from the changelog |

Tags are `vX.Y.Z`, never moved, created only by `gth-release-bot`.
