[← Back to README](../README.md)

# 🩺 Troubleshooting

Start with `rk doctor`: it is read-only and names most problems.

| Symptom | Cause | Fix |
|---|---|---|
| A PR has the `needs-merge` label and a bot comment | Merging its base conflicted, so the bot aborted | In the PR's worktree, `rk wt sync <branch>`, resolve the listed files, commit, run it again |
| The bot's job summary says a push was rejected | The head moved between check and push (someone pushed) | Nothing: the next run reconciles it. Or `rk wt sync` locally |
| A ready PR isn't being updated | It's a draft, from a fork, has a skip label, or is a release-please PR | The job summary gives the reason for each PR |
| `rk wt sync` refuses | Uncommitted or untracked changes | Commit them, or ask what should happen to them |
| `rk wt done` refuses | PR not merged or closed, uncommitted or unpushed work, or the worktree belongs to another repo | Finish the work. The overrides lose work: use them only when that's intended |
| `rk sync` refuses a file | A managed file was edited by hand | Move the change into repo-kit (or a config value), or mark the file `unmanaged` in `[overrides]` |
| `pr-checks / managed-files` fails | Same as above, or `version` was changed without `rk upgrade` | Run `rk upgrade <version>` or `rk sync`, and commit the result |
| `pr-checks / pr-title` fails | The title is a conventional commit in a `not-conventional` repo | Write the title as a plain sentence |
| `doctor` reports ruleset or setting drift | Something was changed in GitHub's UI | Read `rk github diff`, then `rk github apply` (admin, never CI) |
| `doctor` lists a stale worktree | Its branch is gone or its PR has merged | `rk wt done <branch>` |
| `doctor` lists an override | A file is `unmanaged` in `[overrides]` | Intended? Keep it. Otherwise remove the override and `rk sync` |
| A worktree's venv behaves oddly | It's inside OneDrive or another synced folder | Set `worktree_root` outside it in `~/.config/repo-kit/config.toml` |
| `Fatal error in launcher` on Windows | A stale `rk` elsewhere on `PATH` | `where.exe rk`, then remove that copy or reinstall with `uv tool install` |
