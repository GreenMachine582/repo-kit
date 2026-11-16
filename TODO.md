[← Back to README](README.md)

# ✅ TODO

Open work only: remove an item when it ships. Shipped work is in `CHANGELOG.md`. Future items (below) start only
after Phase 4, to keep scope in check.

## Phase 1D: `pr-checks`, `release`, `rk github`, `branches prune`, the `dev-main` profile

Done when unit snapshots of generated rulesets and files pass, and repo-kit runs on its own tooling (dogfood).

- [ ] First release of repo-kit (User approves): set `GTH_RELEASE_BOT_CLIENT_ID` and `GTH_RELEASE_BOT_PRIVATE_KEY`,
  then a `release: …` PR from `dev` to `main`. `release-self.yml` runs release-please. If it picks a version other
  than `0.1.0`, move the pin with `rk upgrade`

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
- [ ] `rk sync` in core, then fastapi, then ui, settling each refused file by hand (each keeps its own
  `use-local-gth.sh`)
- [ ] Delete the old scripts and ruleset JSON in each ([docs/adoption.md](docs/adoption.md#gth-repos))

## Phase 4: remaining repos

Done when each has had one `bootstrap`.

- [ ] PyFinBot
- [ ] Any other repo still carrying its own copies

## 🙋 User-only steps

- [ ] Approve creating the public repo and its first release
- [ ] Install `gth-pr-bot` and `gth-release-bot` on each adopting repo, then run `rk configure` there
- [ ] Run `rk github apply` yourself (needs admin), after reading the diff
- [ ] Approve photo-ledger's switch to `dev` (new default branch, open PRs retargeted) when `rk install` asks

## 🔭 Future

Registered, not planned. Each is picked up on its own, after Phase 4.

- **`use-local-gth` extra:** a managed `scripts/use-local-gth.sh` for GTH consumers, enabled by
  `[extras].use_local_gth`. Until then each GTH repo keeps its own copy.
- **Interactive `rk sync --adopt`:** show each difference and take ownership after confirming, if settling refused
  files by hand proves slow.
- **Renamed and removed templates in `rk upgrade`:** move or delete the file and update the lock.
- **`[project].extra_files`:** release-please `extra-files` in the managed config, for version files such as
  PyFinBot's `src/pyfinbot/version.py`.
- **`rk configure` for `gth-release-bot`:** store its credentials the same way as `gth-pr-bot`'s.
- **Key validation in `rk configure`:** confirm the key and installation before storing, if a stdlib-only way to sign
  the App's JWT turns up.
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
