## 🔀 Branches

```
feat/x ──commits──► draft PR to dev ──► ready ──► checks pass ──► merge commit ──► dev
                                                                                    │
           tag vX.Y.Z ◄── release PR merged ◄── dev → main PR ◄─────────────────────┘
               │
               └──► main merged back into dev
```

- **`dev`** is the default branch and the base for feature PRs.
- **`main`** only holds released code, and only takes release PRs from `dev`. Consumers pin tags (`…@vX.Y.Z`),
  never branches.
- Neither takes a direct push: every change goes through a PR, merged with a **merge commit**.
- Branches are named `<type>/<short-description>` (`feat/folder-picker`, `docs/adoption`), using the commit types
  below. Each holds one topic and lives until its PR merges.
- One branch is one worktree: `rk wt start <branch>` creates both and sets the worktree up. When the PR has merged,
  `rk wt done <branch>` removes them.
