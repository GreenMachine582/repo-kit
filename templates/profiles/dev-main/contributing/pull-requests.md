## 📬 Pull requests

- A PR is one topic, made of related commits. Code and the docs that describe it go in the same PR.
- Open it as a **draft**, and mark it ready when it's done. Only ready PRs are kept current by the PR bot, which
  merges the base in when it moves. Add the `wip` label to a ready PR under rework.
- Update an open PR by adding commits, or by merging its base in with `rk wt sync`. Never rebase or force-push:
  others may have the branch, or have chained a PR on it.

### Commits

PRs are merged with a merge commit, so every commit lands on `dev` as written and release-please reads each one.
**Every commit** must be a [Conventional Commit](https://www.conventionalcommits.org), with a first line of at most
${commit_header_max} characters and a body that explains why. Merge commits are skipped by the `commits` check. `rk install --hooks` runs the same check on each
commit, before it's made.

| Type | For | In the changelog |
|---|---|---|
| `feat` | New behaviour | Yes (minor bump) |
| `fix` | Something that was broken | Yes (patch) |
| `perf` | Faster or lighter, same behaviour | Yes (patch) |
| `build` | Tooling and dependencies | Yes (patch) |
| `docs` | Documentation only | No |
| `refactor` | Restructuring with the same behaviour | No |
| `test` | Tests only | No |
| `ci` | GitHub Actions workflows | No |
| `chore` | Housekeeping that changes no behaviour | No |
| `style` | Formatting only | No |
| `revert` | Undoing an earlier commit | Yes |

A breaking change is `feat!:` (or any type with `!`), plus a `BREAKING CHANGE:` footer in that commit's body
explaining what to change.

### PR titles

${pr_title_rule}
