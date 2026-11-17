[← Back to README](../README.md)

# 🚢 `rk fleet`

`doctor`, `sync --check`, `configure` and `upgrade` across every repo that has adopted repo-kit, from one machine.

| Command | Does | Writes |
|---|---|---|
| `rk fleet list` | Shows `[fleet.repos]` and whether each path is that repo's checkout | Nothing |
| `rk fleet doctor` | `rk doctor` in each repo, one row each | Nothing |
| `rk fleet sync --check` | `rk sync --check` in each repo: which shared files differ, or that the pin trails this rk | Nothing |
| `rk fleet configure [--dry-run] [--replace-key]` | `rk configure` in each repo, asking `Configure <repo>? [y/N]` before each one | GitHub (labels, variables, key secrets), as `rk configure` does |
| `rk fleet upgrade [--apply]` | A dry run by default; with `--apply`, one `rk upgrade` PR per repo | A branch and a PR per repo |

Every command takes `--repo OWNER/NAME` (repeatable) to run on some of the repos. `rk fleet github apply` doesn't
exist: `github apply` stays a per-repo, per-person step that shows its diff and asks.

## Which repos: `[fleet.repos]`

In the user config ([configuration.md](configuration.md#user-config-configrepo-kitconfigtoml)), because the
checkout paths belong to one machine:

```toml
[fleet.repos]
"GreenMachine582/PyFinBot" = "~/dev/PyFinBot"
"GreenMachine582/greentechhub-core" = "~/dev/greentechhub-core"
```

Keys are `owner/name`, values the main checkout (not a worktree). Unknown keys are errors. Before any command runs
on a repo, `rk` checks the path is a Git checkout whose `origin` is that slug, so a wrong path is a failed row, never
a push to the wrong repo. An empty or missing table is an error that shows the lines to add.

## How each repo runs

`fleet` calls rk's own functions once per repo with that checkout as the working directory, never a subprocess
`rk`, so every repo is judged by the engine running now. One repo failing is its own row: the run goes on, and the
exit code is 1 if any row is `FAIL`.

```
GreenMachine582/PyFinBot             OK    in sync
GreenMachine582/greentechhub-core    FAIL  out of sync: release-please-config.json (refused)
GreenMachine582/greentechhub-ui      WARN  14 check(s); WARN: plugin
3 repo(s): 1 ok, 1 warn, 1 fail
```

Statuses are `OK`, `WARN`, `FAIL`, `SKIP`, plus `PLAN` (an upgrade `--apply` would open a PR for) and `PR` (an
upgrade PR is open).

- **`doctor`** runs `rk doctor --json` and reads only `ok` and each check's `name` and `status`, so keys added to the
  document later don't matter. The row is the worst status, with the names of failing and warning checks.
- **`sync --check`** plans the sync without writing. A pin that trails this rk fails like any drift: it is what
  `upgrade` fixes.
- **`configure`** asks before each repo (a typed yes; with no terminal the answer is no) and prints what it changed
  as it goes. `--dry-run` writes nothing and asks nothing.

## `upgrade`

Moves each repo's pin to the release of the rk that is running, so install the release first (`rk upgrade` has the same
rule). For each repo:

1. `git fetch`, then look for an open PR from `chore/upgrade-<version>`. If there is one, the row is `PR` and nothing
   else happens. A branch on `origin` with no open PR is a failure: someone has to look at it.
2. Cut a throwaway worktree from `origin/<base>` (the repo's own default base). The checkout's working tree, branch
   and uncommitted work are never read or changed.
3. Already at this release: `OK`. Otherwise run `rk upgrade` in the worktree. A refusal, such as a managed file with
   local edits, fails that repo before anything is written.
4. Without `--apply`, stop: the row is `PLAN` with the version move and the shared files that change.
5. With `--apply`: check the result passes `rk sync --check` (what CI's `managed-files` runs), commit
   `chore: move repo-kit to <version>`, push the branch (never forced), and open the PR to the base through
   `github.py`. Its title follows the repo's `[checks].pr_title` rule. It's opened ready, not draft. The worktree and
   the local branch are removed, whatever happened.

Re-running is safe: repos with an open PR or already upgraded are left alone, so a run that stopped halfway finishes
what's left. The PRs are never merged by rk. The release notes and `rk github diff` are for a person: a release may
change rulesets or settings, which `fleet` never applies.
