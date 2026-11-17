[← Back to README](README.md)

# ✅ TODO

Open work only: remove an item when it ships. Shipped work is in `CHANGELOG.md`. Items are tagged **(you)** when
only the repo owner can do them (admin rights, App installs, approvals) and **(lead)** when a Claude session can.
Future items (below) start only after Phase 4, unless pulled forward.

## Next: Phase 2, pilot on photo-ledger (`main` only → `dev-main`, pinned to 0.3.0)

Steps are in [docs/adoption.md](docs/adoption.md#photo-ledger). Adoption, the branch model, worktrees, PR updates,
`wt done`, the managed-files and title checks, and live checks 1, 5 and 6 passed on 2026-11-17 (pilot PRs
photo-ledger#38–#46). What's left needs a release. Done when every check below holds:

- **Release** (photo-ledger's first on `dev-main`)
  - [ ] **(you)** Merge the release PR with the admin bypass: `pr-checks / commits` fails on #36's squash commit
    (`ed6c402`), which nothing can rewrite. Once it's on `main`, no later release checks it
  - [ ] **(lead)** The `dev` → `main` release PR passes `pr-title`
  - [ ] **(you)** A release cut through `release.yml` behaves exactly as before
  - [ ] **(lead)** Live checks 2–4 ([docs/testing.md](docs/testing.md#-live-checks-in-the-pilot)): a bot merge adds
    no changelog line, a merged chain gives no duplicates, and the release back-merges, signed. Use real PRs, not
    pilot ones
- **Upgrade** (after the next repo-kit release)
  - [ ] **(lead)** `rk upgrade` produces one clean PR
  - [ ] **(you)** `rk github apply`, which brings back signed commits on `dev` and `main`
- **Worktrees**
  - [ ] **(you)** Claude starts directly in one

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
