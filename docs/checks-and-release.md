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
