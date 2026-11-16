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

## Workflow

`.github/workflows/update-prs.yml` is a reusable workflow. A consumer holds only a caller:

```yaml
jobs:
  update-prs:
    if: vars.GTH_PR_BOT_CLIENT_ID != ''
    permissions:
      contents: read
      pull-requests: read              # for the pre-check
    uses: GreenMachine582/repo-kit/.github/workflows/update-prs.yml@v1.4.0
    with:
      ref: v1.4.0                      # the same version: the rk code to run
      client-id: ${{ vars.GTH_PR_BOT_CLIENT_ID }}
      dry-run: ${{ github.event_name == 'workflow_dispatch' && inputs.dry-run }}
      conflict-label: "needs-merge"    # [update_prs].conflict_label, written by rk sync
    secrets:
      PR_BOT_PRIVATE_KEY: ${{ secrets.GTH_PR_BOT_PRIVATE_KEY }}
```

- **What's checked out:** only repo-kit, at `ref`. No code from the consumer repo or its PRs reaches the runner.
- **How rk runs:** from source (`python -m repo_kit` with `PYTHONPATH`). It's standard library only, so nothing is
  installed next to the token.
- **The token:** minted for this repo only, with contents, pull requests and issues write, and only once the
  pre-check (see [Triggers](#triggers)) says the run is needed. `GITHUB_TOKEN` gets only `contents: read`, plus
  `pull-requests: read` in the pre-check.
- **Fork PRs:** their `pull_request` runs get no secrets, so the job skips them.
- **Before enrolment:** the `if:` keeps the caller idle until `rk configure` has enrolled the repo.
- **repo-kit's own caller:** `.github/workflows/keep-prs-current.yml` passes the default branch as `ref`, so only merged
  rk code runs with the token.

## Triggers

| Event | Types |
|---|---|
| `push` | All branches, so a chain's child hears about a push to its parent. The [pre-check](#push-pre-check) skips most |
| `pull_request` | `edited`, `ready_for_review`, `reopened`, `closed` |
| `workflow_dispatch` | With a `dry-run` input, ticked by default |

### Push pre-check

Most pushes are to a feature branch nothing depends on, and reconciling for them would change nothing. So a first job,
`pre-check`, runs with `GITHUB_TOKEN` only (`contents: read`, `pull-requests: read`, no App token and no checkout) and
decides whether the `update-prs` job runs at all. `pull_request` and `workflow_dispatch` events always run. A push
runs it only when the pushed branch is:

- the **base** of a ready, non-fork open PR: that PR may now be behind; or
- the **head** of a ready, non-fork open PR that carries the conflict label: the author may have resolved it with
  `rk wt sync`, and the run clears the label; or
- the **head** of a ready, non-fork open PR that is **behind its base**. A head push can't make a PR fall behind, but
  it may already be: an earlier update was refused because the head moved (often this very push racing a base push),
  GitHub never worked out mergeability, or the call failed. Without this case such a PR would wait for the next push
  to its base.

Any other push, a tag push or a branch deletion skips `update-prs`, so no token is minted. The decision and its
reason are shown as a notice on the run. If the pre-check itself fails, the run fails and `update-prs` is skipped;
the next run reconciles everything anyway.

Why only these: for a PR whose head was pushed, reconcile can only clear a leftover label (needs the label), merge
the base in (needs the PR to be behind) or flag a conflict (also needs it to be behind: a head that already has every
base commit can't conflict with its base). Every other PR is unaffected by a push to a branch that isn't its base.
The rule leaves out some of reconcile's skip reasons (skip labels, release-please and long-lived heads); those
pushes still run, and reconcile skips them as before.

The pre-check is shell and `gh api`, not rk, so it works with whichever rk `ref` names (repo-kit's own caller runs
the default branch's rk). The conflict label it looks for comes from the `conflict-label` input: `rk sync` writes
`[update_prs].conflict_label` into the consumer's caller, and its default is `needs-merge`.

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
