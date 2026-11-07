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
| 2 | Conflicting base merge | No push; label; one comment |
| 3 | Re-run on an updated PR | No-op |
| 4 | Two PRs sharing a base | Both updated |
| 5 | Chain of three | Parents before children, one run |
| 6 | Base retargeted after parent merges | Reconciled against the new base |
| 7 | Head moves between check and push | Rejected, recorded, nothing lost |
| 8 | Bot-updated branch, local session diverged | `wt sync` merges both and pushes |
| 9 | Draft, fork, `wip`, release-please PRs | Skipped with the right reason |
| 10 | Interrupted merge | Next run clean |
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
| 18 | `upgrade` across a release that adds, renames and removes a template | Added, moved, deleted; lock updated |
| 19 | `--adopt` on a copy of a GTH repo | Differences listed; nothing written until confirmed |

## 🔴 Live checks in the pilot

Once, against real GitHub, in Phase 2:

1. The `pr-checks` check names match the generated ruleset.
2. A bot merge commit passes `commits` and adds nothing to the changelog.
3. A merged chain gives no duplicate changelog lines.
4. `dev` → `main` produces the intended release and back-merge.
5. `github diff` is empty right after `github apply`.

## CI

ruff (lint and format), `actionlint`, `claude plugin validate`. Worktree and sync scenarios run on Linux and Windows.
