[← Back to README](README.md)

# ✅ TODO

Open work only: remove an item when it ships. Shipped work is in `CHANGELOG.md`. Future items (below) start only
after Phase 4, to keep scope in check.

## Phase 1A: engine core, config, worktree commands, three skills

Done when scenarios 8, 11 and 12 pass ([docs/testing.md](docs/testing.md)), the skills load, and CI is green on Linux
and Windows.

- [ ] `config.py`: `.repo-kit.toml` and user config, unknown keys rejected ([docs/configuration.md](docs/configuration.md))
- [ ] `git.py`, `github.py` (with a fake PR store for tests)
- [ ] `rk wt start / sync / done` ([docs/worktrees.md](docs/worktrees.md))
- [ ] Worktree path slug and hash
- [ ] Scenario harness: bare origin + clones in `tmp_path`
- [ ] Confirm the minimum Git version (2.38+ assumed)
- [ ] `claude plugin validate` and `actionlint` in CI

## Phase 1B: `gth-pr-bot` and `update-prs`

Done when scenarios 1–7, 9 and 10 pass.

- [ ] Create the `gth-pr-bot` App ([docs/github-app.md](docs/github-app.md))
- [ ] `reconcile.py` and `rk update-prs` ([docs/pr-updates.md](docs/pr-updates.md))
- [ ] Reusable `.github/workflows/update-prs.yml`

## Phase 1C: `sync`, install, configure, upgrade, doctor

Done when scenarios 13–19 pass and a fresh repo reaches `doctor` all-OK in one `bootstrap`.

- [ ] `files.py`: render, `.repo-kit.lock`, ownership modes, `--check`, `--adopt` ([docs/shared-files.md](docs/shared-files.md))
- [ ] `rk install` (including creating `dev` and retargeting PRs), `configure`, `bootstrap`, `upgrade`
- [ ] `rk doctor` ([docs/adoption.md](docs/adoption.md))
- [ ] `templates/common/`: callers, `.gitignore` block, CONTRIBUTING blocks
- [ ] `templates/extras/use-local-gth/`

## Phase 1D: `pr-checks`, `release`, `rk github`, `branches prune`, the `dev-main` profile

Done when unit snapshots of generated rulesets and files pass, and repo-kit runs on its own tooling (dogfood).

- [ ] `checks.py` and the `commit-msg` hook ([docs/checks-and-release.md](docs/checks-and-release.md))
- [ ] Reusable `pr-checks.yml` and `release.yml`
- [ ] `rulesets.py`, `settings.py`, `rk github diff / apply` ([docs/rulesets.md](docs/rulesets.md))
- [ ] `rk branches prune`
- [ ] `templates/profiles/dev-main/`, including `release-please-config.json`
- [ ] Dogfood: `.repo-kit.toml`, `.repo-kit.lock`, generated callers replace the hand-written `ci.yml`
- [ ] First release of repo-kit (User approves)

## Phase 2: pilot on photo-ledger, moving from `main` only to `dev-main`

Steps are in [docs/adoption.md](docs/adoption.md#photo-ledger). Done when every check below holds:

- **Branch model**
  - [ ] `dev` exists and is the default branch
  - [ ] Open PRs were retargeted to `dev`
  - [ ] A `dev` → `main` release PR passes `pr-title`
- **Worktrees**
  - [ ] Two photo-ledger worktrees run side by side with separate environments
  - [ ] Claude starts directly in one
- **PR updates**
  - [ ] A merge to `dev` updates every eligible ready PR, and CI re-runs
  - [ ] Drafts and `wip` PRs are skipped with reasons
  - [ ] A deliberate conflict gets `needs-merge` and exactly one comment; `wt sync` clears it
  - [ ] Re-runs create no duplicates; a concurrent local commit is never lost
  - [ ] A chain of three stays current; a cancelled run re-runs safely
- **Cleanup**
  - [ ] `wt done` refuses every unsafe state
- **Shared files**
  - [ ] Hand-editing a managed file fails `pr-checks / managed-files`
  - [ ] `rk upgrade` to a new repo-kit release produces one clean PR
  - [ ] `github diff` shows no drift
- **Checks and release**
  - [ ] A conventional PR title fails `pr-title`, and a release PR passes it
  - [ ] A release cut through `release.yml` behaves exactly as before
- **Live checks** ([docs/testing.md](docs/testing.md#-live-checks-in-the-pilot))
  - [ ] All five pass

## Phase 3: GTH repos (`dev-main`, migrating)

Done when `doctor` is all-OK in each, one release is cut per repo through the new `release.yml`, and a FastAPI
worktree links a Core worktree.

- [ ] `local_gth.py` resolution order change in greentechhub-core ([docs/worktrees.md](docs/worktrees.md#-local-gth-mode))
- [ ] `rk sync --adopt` in core, then fastapi, then ui
- [ ] Delete the old scripts and ruleset JSON in each ([docs/adoption.md](docs/adoption.md#gth-repos))

## Phase 4: remaining repos

Done when each has had one `bootstrap`.

- [ ] PyFinBot
- [ ] Any other repo still carrying its own copies

## 🙋 User-only steps

- [ ] Approve creating the public repo and its first release
- [ ] Create `gth-pr-bot`, install it and `gth-release-bot` on adopting repos, and supply keys when `rk configure` asks
- [ ] Run `rk github apply` yourself (needs admin), after reading the diff
- [ ] Approve photo-ledger's switch to `dev` (new default branch, open PRs retargeted) when `rk install` asks

## 🔭 Future

Registered, not planned. Each is picked up on its own, after Phase 4.

- **`main-only` profile**, if a repo ever needs a trunk-only flow: ruleset builder and templates without `dev` or
  back-merge.
- **Daily safety-net run** of `update-prs`, if missed events ever show up in practice.
- **Upgrade fan-out:** on a repo-kit release, `gth-pr-bot` opens an `rk upgrade` PR in every adopting repo.
- **Private-branch rebase** in `wt sync`: rebase only branches nothing depends on.
- **Deterministic conflict handling:** `rerere`, lockfile regeneration, per-path rules.
- **AI-assisted conflict resolution:** a local Haiku-class agent first; in CI only as resolve (no GitHub credentials)
  → verify (no secrets) → publish (App token), in `suggest` mode.
- **Move `next-task` and `open-pr` into the plugin**, with per-repo specifics from config.
- **More managed files** as patterns repeat: `ruff`/pytest config blocks, PR template, issue templates, `CODEOWNERS`.
