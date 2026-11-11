[← Back to README](../README.md)

# 🧪 Testing

Tests only ever touch `tmp_path`: never a real repo, never GitHub.

## Unit (`tests/unit/`)

- Config parsing, including unknown keys
- Worktree slug and hash
- PR ordering and skip rules
- Commit and title rules: a table of valid and invalid examples, including GitHub's merge-commit formats
- Ruleset and settings builders: a snapshot per profile
- `string.Template` rendering
- The CLI command tree

## Git scenarios (`tests/scenarios/`)

pytest against a bare origin and clones, with a fake PR store behind the `github.py` interface.

| # | Scenario | Expected |
|---|---|---|
| 1 | Clean base merge | One merge commit pushed |
| 2 | Conflicting base merge | No update; label; one comment |
| 3 | Re-run on an updated PR | No-op |
| 4 | Two PRs sharing a base | Both updated |
| 5 | Chain of three | Parents before children; one run against the fake, which merges at once (on GitHub, up to one run per link) |
| 6 | Base retargeted after parent merges | Reconciled against the new base |
| 7 | Head moves between check and update | Refused, recorded, nothing lost |
| 8 | Bot-updated branch, local session diverged | `wt sync` merges both and pushes |
| 9 | Draft, fork, `wip`, release-please and `dev` → `main` PRs | Skipped with the right reason |
| 10 | A run that fails part-way | The other PRs still update; next run clean |
| 11 | `wt done` on each unsafe state | Refuses with the matching message |
| 12 | Colliding branch slugs | Distinct paths |

## File sync scenarios

| # | Scenario | Expected |
|---|---|---|
| 13 | Fresh `install`, including a repo with only `main` | Expected file set; lock written; `dev` created; PRs retargeted only after confirmation |
| 14 | `sync` twice | Second run changes nothing |
| 15 | Managed file edited by hand | `sync` refuses with diff; `--check` fails |
| 16 | Block edited outside the markers | Consumer's text kept |
| 17 | Seeded file changed | Never overwritten |
| 18 | `upgrade` across a release that adds a template | Added; lock updated. Renames and removals are a Future item |
| 19 | `sync` in a repo that already has a managed file | Taken over if it matches the render; otherwise refused with the diff. Interactive `--adopt` is a Future item |

## 🔴 Live checks in the pilot

Once, against real GitHub, in Phase 2:

1. The `pr-checks` check names match the generated ruleset.
2. A bot merge commit passes `commits` and adds nothing to the changelog.
3. A merged chain gives no duplicate changelog lines.
4. `dev` → `main` produces the intended release and back-merge.
5. `github diff` is empty right after `github apply`.

## CI

ruff (lint and format), mypy (strict, on `src/`), `actionlint`, `zizmor` (a security audit of the workflows) and
`claude plugin validate`. Worktree and sync scenarios run on Linux and Windows. Third-party actions are pinned to a
full commit SHA, and Dependabot moves the pins.
