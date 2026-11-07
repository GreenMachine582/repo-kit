[← Back to README](../README.md)

# 🔄 PR updates: `update-prs`

A reusable workflow that keeps every ready PR current with its base, run as `gth-pr-bot`. It never merges a PR and
never force-pushes.

## Reconcile state, don't replay events

Every run looks at everything, so a dropped or cancelled run is harmless:

1. List **all** open PRs, paginated, with live `base.ref`, `head.ref` and head SHA.
2. Drop ineligible PRs and record why: **draft**, fork, skip label, release-please branch, already up to date.
3. Order parents before children, so a chain `dev ← A ← B ← C` updates in one run.
4. For each PR: re-check the skip labels, merge `origin/<base>` into the head, then push **without force**.

   | Outcome | What happens |
   |---|---|
   | Accepted | CI re-runs (the push is made with an App token); the conflict label is removed |
   | Rejected (the head moved) | Recorded; the next run reconciles it |
   | Conflict | Merge aborted; the conflict label is added; **one** comment, found again by a hidden marker, lists the files and `rk wt sync <branch>` |

5. Write a job summary: one row per PR, with the action taken or the skip reason.

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

Credentials and permissions are in [github-app.md](github-app.md).
