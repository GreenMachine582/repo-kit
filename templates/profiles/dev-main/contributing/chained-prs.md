## 🔗 Several PRs at once

Small PRs are reviewed faster and conflict less. When one piece of work builds on another, chain them instead of
opening one big PR:

1. `feat/a` → a PR into `${default_base}`.
2. `rk wt start feat/b --from feat/a` → a PR with **base `feat/a`**. Its diff shows only B's commits, and the checks
   run on it as usual.
3. Merge A. GitHub deletes `feat/a` and retargets B's PR to `${default_base}` by itself. B already holds A's commits
   (merge commits keep them), so nothing conflicts and nothing needs rebasing.
4. Merge B, and so on down the chain.

Merge the chain in order, starting from the PR nearest `${default_base}`: merging B first would land A's commits with
it. To pick up a fix made in A after B was branched, merge `feat/a` into `feat/b`.
