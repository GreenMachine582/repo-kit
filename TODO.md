[← Back to README](README.md)

# ✅ TODO

Open work only: remove an item when it ships. Shipped work is in `CHANGELOG.md`. Items are tagged **(you)** when
only the repo owner can do them (admin rights, App installs, approvals) and **(lead)** when a Claude session can.
Future items (below) start only after Phase 4, unless pulled forward.

## Next: Phase 2, pilot on photo-ledger (`main` only → `dev-main`, pinned to 0.3.0)

Steps are in [docs/adoption.md](docs/adoption.md#photo-ledger). Done when every check below holds:

- **Setup**
  - [ ] **(you)** `rk configure`, then `rk github apply` after reading the diff, then merge the adoption PR
    (photo-ledger#36)
  - [ ] **(you)** After the next repo-kit release: `rk upgrade` and `rk github apply` in photo-ledger, which brings
    back signed commits (its old `main` ruleset required them; 0.3.0's rulesets don't)
- **Branch model**
  - [ ] **(lead)** `dev` exists and is the default branch; open PRs were retargeted to `dev`
  - [ ] **(lead)** A `dev` → `main` release PR passes `pr-title`
- **Worktrees**
  - [ ] **(lead)** Two photo-ledger worktrees run side by side with separate environments
  - [ ] **(you)** Claude starts directly in one
- **PR updates**
  - [ ] **(lead)** A merge to `dev` updates every eligible ready PR, and CI re-runs
  - [ ] **(lead)** Drafts and `wip` PRs are skipped with reasons
  - [ ] **(lead)** A deliberate conflict gets `needs-merge` and exactly one comment; `wt sync` clears it
  - [ ] **(lead)** Re-runs create no duplicates; a concurrent local commit is never lost
  - [ ] **(lead)** A chain of three stays current; a cancelled run re-runs safely
- **Cleanup**
  - [ ] **(lead)** `wt done` refuses every unsafe state
- **Shared files**
  - [ ] **(lead)** Hand-editing a managed file fails `pr-checks / managed-files`
  - [ ] **(lead)** `rk upgrade` to a new repo-kit release produces one clean PR
  - [ ] **(you)** `github diff` shows no drift
- **Checks and release**
  - [ ] **(lead)** A conventional PR title fails `pr-title`, and a release PR passes it
  - [ ] **(you)** A release cut through `release.yml` behaves exactly as before
- **Live checks** ([docs/testing.md](docs/testing.md#-live-checks-in-the-pilot))
  - [ ] **(lead)** All six pass

## Phase 3: GTH repos (`dev-main`, migrating)

Done when `doctor` is all-OK in each, one release is cut per repo through the new `release.yml`, and a FastAPI
worktree links a Core worktree.

- [ ] **(lead)** `local_gth.py` resolution order change in greentechhub-core
  ([docs/worktrees.md](docs/worktrees.md#-local-gth-mode))
- [ ] **(you)** Install both Apps, `rk configure` and `rk github apply` in core, fastapi and ui
- [ ] **(lead)** `rk sync --adopt` in core, then fastapi, then ui, settling each refused file (each keeps its own
  `use-local-gth.sh`)
- [ ] **(lead)** Delete the old scripts and ruleset JSON in each ([docs/adoption.md](docs/adoption.md#gth-repos))
- [ ] **(lead)** Add each to `[fleet.repos]`; `rk fleet doctor` is all-OK

## Phase 4: remaining repos

Done when each has had one `bootstrap`.

- [ ] **(you + lead)** PyFinBot, with `[project].extra_files` for `src/pyfinbot/version.py`
- [ ] **(you + lead)** Any other repo still carrying its own copies

## 🔭 Future

Registered, not planned. Each is picked up on its own, after Phase 4.

- **`use-local-gth` extra:** a managed `scripts/use-local-gth.sh` for GTH consumers, enabled by
  `[extras].use_local_gth`. Until then each GTH repo keeps its own copy.
- **Key validation in `rk configure`:** confirm the key and installation before storing, if a stdlib-only way to sign
  the App's JWT turns up.
- **`main-only` profile**, if a repo ever needs a trunk-only flow: ruleset builder and templates without `dev` or
  back-merge.
- **Daily safety-net run** of `update-prs`, if missed events ever show up in practice.
- **Upgrade fan-out:** on a repo-kit release, `gth-pr-bot` opens an `rk upgrade` PR in every adopting repo. Until
  then, `rk fleet upgrade --apply` does it by hand.
- **zizmor in consumers:** run it on each consumer's workflows, as repo-kit's own CI does.
- **Private-branch rebase** in `wt sync`: rebase only branches nothing depends on.
- **Deterministic conflict handling:** `rerere`, lockfile regeneration, per-path rules.
- **AI-assisted conflict resolution:** a local Haiku-class agent first; in CI only as resolve (no GitHub credentials)
  → verify (no secrets) → publish (App token), in `suggest` mode.
- **Move `next-task` and `open-pr` into the plugin**, with per-repo specifics from config.
- **More managed files** as patterns repeat: `ruff`/pytest config blocks, PR template, issue templates, `CODEOWNERS`,
  `SECURITY.md`.
