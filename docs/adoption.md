[← Back to README](../README.md)

# 📦 Adoption, upgrades and `doctor`

## Commands

| Command | Does |
|---|---|
| `rk install [--profile …] [--hooks]` | Seed `.repo-kit.toml`, then `sync`. Add the plugin marketplace to `.claude/settings.json`. If the profile needs `dev` and the repo only has `main`: create `dev` from `main`, then (after showing the list and asking) retarget open PRs from `main` to `dev`. The default-branch switch itself is a repo setting, applied by `configure` |
| `rk configure [--dry-run] [--replace-key]` | Enrol the repo for `gth-pr-bot`: create the update-prs labels, and set the Client ID variable and private key secret from the user config ([github-app.md](github-app.md#enrolling-a-repo)). `github apply` joins it in Phase 1D |
| `rk bootstrap` | `install` + `configure` + `doctor` |
| `rk upgrade [vX.Y.Z]` | Change `version`, run `sync`, print migration notes for every release in between, then show `github diff` |
| `rk doctor` | Read-only health check |

Every write checks tool versions and the remote, backs up what it replaces, and finishes with a list of exactly what
changed.

## `doctor`

```
[OK]   git 2.47 / gh 2.63 / Python 3.12
[OK]   .repo-kit.toml valid (profile dev-main, version 1.4.0; latest 1.5.0)
[OK]   managed files: 9 in sync, 0 drifted
[OK]   rulesets: repo-kit: dev, repo-kit: main, repo-kit: tags match config
[WARN] repo setting drift: allow_squash_merge is true (expected false)
[OK]   gth-pr-bot, gth-release-bot installed; secrets present; token mint works
[WARN] override in use: .github/workflows/release.yml is unmanaged
[WARN] 1 stale worktree: ~/dev/wt/GreenMachine582-photo-ledger/fix-x-1a2b3c
```

Minimum versions, checked by `doctor`: Git 2.38+ (`git.MIN_GIT`; every `rk wt` command checks it too), gh 2.40+, Python 3.11+.

## 🏷️ Versioning

- repo-kit itself: release-please, immutable `vX.Y.Z` tags, no floating `v1`. It manages its own repo with `rk`.
- **One pin per consumer:** `version` in `.repo-kit.toml`. Every generated caller uses `@v<version>` and passes that
  same version to the engine. Bumping the pin is always `rk upgrade`, so callers, managed files and rulesets move
  together.
- **Dependabot is not used for repo-kit refs.** It would bump `uses:` lines without re-rendering the other files, and
  the drift check would fail. If Dependabot is enabled for other actions, rk adds an `ignore` entry for
  `GreenMachine582/repo-kit`.
- Upgrades are one PR per repo, made with `rk upgrade`. Fanning them out automatically on release is a Future item.
- Breaking changes are `feat!:` with migration notes, which `rk upgrade` prints.

## Per-repo changes

### photo-ledger

Moves from `main` only to `dev-main` (Phase 2).

1. Finish Phase 0 first: #35 still targets `main`.
2. `rk bootstrap --profile dev-main`:
   - creates `dev` from `main`, and asks before retargeting open PRs to `dev`;
   - sets `dev` as the default branch;
   - creates `.repo-kit.toml`, `.repo-kit.lock`, the caller workflows (`update-prs`, `pr-checks`, `release`),
     `release-please-config.json`, the manifest (seeded from the current `pyproject.toml` version), the `.gitignore`
     block and the CONTRIBUTING blocks (including the branch diagram with `dev`).
3. By hand:
   - `next-task` step 5 uses `worktree-start`;
   - `open-pr` targets `dev`, opens drafts, marks them ready, and syncs with `worktree-sync`;
   - `CLAUDE.md` gets a short prose note pointing to `.repo-kit.toml`.
4. Local clones need `git fetch` and a checkout of `dev`.

### GTH repos

Already `dev-main`; migrating (Phase 3), in the order core → fastapi → ui.

1. `rk sync` takes over each existing workflow and `use-local-gth.sh` once it matches the render; where one differs,
   it refuses with the diff, and the difference is settled by hand (a config value, an `[overrides]` entry, or
   dropping it). The existing manifest is kept (seeded).
2. Delete `scripts/apply-rulesets.sh`, `scripts/prune-branches.sh`, `.github/rulesets/*.json` and
   `.github/repo-settings.json`.
3. `rk github diff` must show no unexpected changes before `apply`.
4. The CONTRIBUTING sections become managed blocks. Repo-specific sections such as *Developing against local GTH
   repos* stay outside the markers.
5. greentechhub-core's `local_gth.py` gets the new resolution order ([worktrees.md](worktrees.md#-local-gth-mode)).

### Other repos

PyFinBot and the rest (Phase 4): one `rk bootstrap` each.
