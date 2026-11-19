[← Back to README](README.md)

# ✅ TODO

Open work only: remove an item when it ships. Shipped work is in `CHANGELOG.md`. Items are tagged **(you)** when
only the repo owner can do them (admin rights, App installs, approvals) and **(lead)** when a Claude session can.
Future items (below) start only after Phase 4, unless pulled forward.

## Next: Phase 2, pilot on photo-ledger (`main` only → `dev-main`, pinned to 0.4.0)

Steps are in [docs/adoption.md](docs/adoption.md#photo-ledger). On 2026-11-17 and 2026-11-18 these passed:
- adoption, the branch model and worktrees;
- PR updates and `wt done`;
- the managed-files and title checks (pilot PRs photo-ledger#38–#46);
- the first release, v0.9.1 (photo-ledger#47, #48): `pr-title` passed on the release PR, it was tagged by
  gth-release-bot, and the back-merge reached `dev`, verified;
- live checks 1, 4, 5 and 6;
- the upgrade to 0.4.0: `rk upgrade` gave one clean PR (photo-ledger#49) that pinned the callers by commit, and
  `rk github apply` brought back signed commits on `dev` and `main`.

v0.9.0 had to be published by hand first
([docs/adoption.md](docs/adoption.md#a-release-pr-merged-but-never-published)). Done when every check below holds:

- **Changelog** ([docs/testing.md](docs/testing.md#-live-checks-in-the-pilot))
  - [ ] **(lead)** Live checks 2 and 3, at the next photo-ledger release, from real PRs: a merged PR that update-prs
    updated adds no changelog line for the bot's merge, and a merged chain gives no duplicates
- **Worktrees**
  - [ ] **(you)** Claude starts directly in one

## Phase 3: GTH repos (`dev-main`, migrating)

Done when `doctor` is all-OK in each, one release is cut per repo through the new `release.yml`, and a FastAPI
worktree links a Core worktree.

The steps are in [docs/adoption.md](docs/adoption.md#gth-repos). On 2026-11-19 greentechhub-core was adopted
(core#98), with `configure` and `github apply` run and the old files deleted (core#99). `doctor` is all-OK there.

- [ ] **(lead)** `doctor`'s `ref-pin` WARN ("tag vX.Y.Z only") says "`rk upgrade` records one", but at the same pin
  `upgrade` only prints "already at X.Y.Z". Since #75, the fix is `rk sync`, so point the WARN at it
  (`src/repo_kit/doctor.py`, the `ref-pin` check, and its test)
- [ ] **(lead)** One release of core through the new `release.yml`, after checking for an unpublished release PR
- [ ] **(you)** Install both Apps on fastapi and ui
- [ ] **(lead)** Adoption PR in fastapi, then ui, settling each refused file (each keeps its own `use-local-gth.sh`)
- [ ] **(you)** In each, after its adoption PR merges: `rk configure`, `rk github diff` and `apply`, then delete the
  old rulesets
- [ ] **(lead)** Cleanup PR in each: the old workflows, scripts and ruleset JSON
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
