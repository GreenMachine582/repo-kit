[← Back to README](../README.md)

# 🔄 PR updates: `update-prs`

A reusable workflow that keeps every ready PR current with its base, run as `gth-pr-bot`. It never merges a PR and
never force-pushes.

## Reconcile state, don't replay events

Every run looks at everything, so a dropped or cancelled run is harmless:

1. List **all** open PRs, paginated, with live `base.ref`, `head.ref` and head SHA.
2. Drop ineligible PRs and record why: **draft**, fork, skip label, release-please branch, or a head that is a
   long-lived branch (the `dev` → `main` release PR).
3. Order parents before children, so a chain `dev ← A ← B ← C` converges. GitHub does each update asynchronously, so a
   chain can take one run per link; each update's push starts the next run.
4. For each PR: read it again for its current labels, draft state and base, and ask GitHub how it stands against that
   base. While GitHub is still working out mergeability, wait and ask again, up to 4 times.

   | State | What happens |
   |---|---|
   | Up to date | Nothing, apart from removing a leftover conflict label |
   | Behind | GitHub's update-branch API merges the base into the head, guarded by the head SHA from step 1. No force, no clone, and no PR code on the runner |
   | Update accepted | CI re-runs (the update is made with an App token); the conflict label is removed |
   | Update refused (such as the head having moved) | Recorded; the next run reconciles it |
   | Conflict | The conflict label is added; **one** comment, found again by a hidden marker, names the base and `rk wt sync <branch>`. GitHub doesn't report which files conflict; `rk wt sync` lists them |
   | Still unknown | Recorded; nothing changes |

   A failed API call for one PR is recorded, the other PRs still run, and the command exits non-zero.

5. Write a job summary: one row per PR, with the action taken or the skip reason.

`rk update-prs --dry-run` reports all of this and writes nothing.

## Triggers

| Event | Types |
|---|---|
| `push` | All branches |
| `pull_request` | `edited`, `ready_for_review`, `reopened`, `closed` |
| `workflow_dispatch` | With a `dry_run` input |

No schedule for now; a daily safety net is a Future item. Concurrency is one group per repo, without cancelling runs
in progress. A dropped pending run is harmless, because any run covers everything.

## Work in progress = draft

Sessions open PRs as drafts and mark them ready when done. The bot only touches ready PRs. Add `wip` to a ready PR
under rework. Because the bot never force-pushes, in any remaining race the losing push catches up through
`rk wt sync`.

## Configuration

```toml
[update_prs]
enabled = true
skip_labels = ["no-auto-update", "wip"]
conflict_label = "needs-merge"
```

The command runs without a checkout of the repo. It reads `.repo-kit.toml` through the API from the default branch, as
data, so a PR can't change how the bot treats it. The repo comes from `GITHUB_REPOSITORY` in Actions and from `origin`
locally.

Credentials and permissions are in [github-app.md](github-app.md).
