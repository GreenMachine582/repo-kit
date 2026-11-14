[← Back to README](../README.md)

# 📦 Adoption, upgrades and `doctor`

## Commands

| Command | Does |
|---|---|
| `rk install [--profile …] [--hooks]` | Seed `.repo-kit.toml`, then `sync`. Add the plugin marketplace to `.claude/settings.json`. If the profile needs `dev` and the repo only has `main`: create `dev` from `main`, then (after showing the list and asking) retarget open PRs from `main` to `dev`. The steps are [below](#install-step-by-step) |
| `rk configure [--dry-run] [--replace-key]` | Enrol the repo for `gth-pr-bot`: create the update-prs labels, and set the Client ID variable and private key secret from the user config ([github-app.md](github-app.md#enrolling-a-repo)). `github apply` joins it in Phase 1D |
| `rk bootstrap` | `install` + `configure` + `doctor`. It reads `gth-pr-bot`'s key first, so a machine that can't enrol the repo stops before anything is written. The `doctor` report ends the run |
| `rk upgrade [vX.Y.Z]` | Move `version` to this rk's release and re-render with the `sync` planner. Templates new in the release are added. Migration notes and `github diff` arrive in Phase 1D. The steps are [below](#upgrade-step-by-step) |
| `rk doctor` | Read-only health check. The checks are [below](#doctor) |

Every write checks tool versions and the remote, backs up what it replaces, and finishes with a list of exactly what
changed.

## `install`, step by step

Run it in a checkout of the repo, on the branch the adoption PR will come from. It commits nothing: the `Changed:`
list is what to review and commit.

1. **Config.** An existing `.repo-kit.toml` is used as it is. Otherwise the first one is built: `version` is the
   running rk's version, `[project].name` is the repo name, and `release_type` is `python` with a `pyproject.toml`,
   `node` with a `package.json`, else `simple`. An unreleased rk (`0.0.0`) has no release to pin, so it refuses to
   seed: install a release, or write `.repo-kit.toml` with a `version` first.
2. **Checks, before any write.** The sync is planned (a refused file stops here, with its diff),
   `.claude/settings.json` is read, and `origin` is fetched to see which branches exist.
3. **Files.** The sync writes the shared files, `.repo-kit.toml` among them, and the lock
   ([shared-files.md](shared-files.md)).
4. **Plugin.** `.claude/settings.json` gains the `repo-kit` marketplace and plugin, only where missing: other keys,
   a marketplace source the repo chose and a plugin it turned off are kept.
5. **Base branch.** If `origin` has `main` but no `dev`, `dev` is created from `main` with a plain push. Making it
   the default branch is a repo setting, applied by `rk github apply` (Phase 1D).
6. **Open PRs.** Every open PR that targets `main` from a branch other than `dev` is listed, and retargeted to
   `dev` only after a yes. It looks at the PRs as they are now, so a run that was declined can be finished by running
   `install` again. With no terminal to ask on, nothing is retargeted.

`--hooks` also installs the `commit-msg` hook ([checks-and-release.md](checks-and-release.md#the-commit-msg-hook)),
which is checked with the other steps before anything is written.

## `upgrade`, step by step

Templates ship inside rk, so rk can only upgrade **to its own release**: install rk X.Y.Z first (`uv tool install
git+https://github.com/GreenMachine582/repo-kit@vX.Y.Z`), then run `rk upgrade`. Like `install`, it commits nothing.

1. **Target.** No argument means this rk's version. `vX.Y.Z` and `X.Y.Z` are both accepted, and must equal the running
   rk's version. An unreleased rk (`0.0.0`) accepts any explicit target, and needs one; it warns that its templates
   may differ from that release. A target below the current pin (a downgrade) is refused.
2. **Checks, before any write.** The sync is planned with the new pin. A managed file with local edits stops here,
   with its diff, and nothing is written. `.repo-kit.toml` must hold exactly one top-level `version = "…"` line.
3. **Files.** The plan is applied: managed files and blocks that use the version are re-rendered, and templates that
   are new in this release are created and entered in the lock.
4. **Pin.** The `version` line of `.repo-kit.toml` is rewritten last, so a failed write leaves the old pin and a
   re-run finishes the job. Comments, other keys and the quote style are kept. The old file is backed up to
   `.git/repo-kit/backup/<UTC>/`.
5. **Notices.** Migration notes are not built yet: `upgrade` points to the release notes, and to `rk github diff`,
   since a release may change the rulesets or repo settings. Templates renamed or removed in the release are reported
   as "not a template" and left in place: move or delete them by hand.

Running it again at the same pin prints "already at X.Y.Z" and changes nothing.

## `doctor`

```
[OK]   git 2.47 / gh 2.63 / Python 3.12
[OK]   .repo-kit.toml valid (profile dev-main, version 1.4.0)
[OK]   user config valid
[OK]   managed files: 9 in sync, 0 drifted
[WARN] override in use: .github/workflows/release.yml is unmanaged
[OK]   plugin: repo-kit is in .claude/settings.json
[OK]   base branch: origin/dev exists (as of the last fetch)
[OK]   gth-pr-bot: labels, Client ID variable and private key secret present
[SKIP] gth-pr-bot installed and token mint
[OK]   repo settings and rulesets match: repo-kit: dev, repo-kit: main, repo-kit: tags
[SKIP] repo settings
[WARN] 1 stale worktree
       ~/dev/wt/GreenMachine582-photo-ledger/fix-x-1a2b3c (fix/x: PR #41 merged)
       remove each with `rk wt done <branch>`
```

It writes nothing and doesn't fetch, so remote branches are as of the last `git fetch`. One line per check, with the
command that fixes it underneath. It exits 1 if any line is `[FAIL]`; `[WARN]` and `[SKIP]` don't fail it.

| Check | `[FAIL]` or `[WARN]` when |
|---|---|
| Tools | FAIL: Git, gh or Python is below its minimum. WARN: gh isn't installed (`sync` and `install` work without it) |
| `.repo-kit.toml` | FAIL: missing, an unknown key or a bad value, or `version` empty or not `X.Y.Z`. WARN: the pin isn't this rk's release, and the files check is skipped until `rk upgrade` |
| User config | FAIL: an unknown key, or a synced `worktree_root` or key path |
| Managed files | FAIL: a file is missing, differs from the render, was edited by hand, or the lock is stale: the same test as `rk sync --check`. WARN: a lock entry is no longer a template |
| Overrides | WARN: each `unmanaged` file |
| Plugin | WARN: `.claude/settings.json` lacks the repo-kit marketplace or plugin (`rk install`) |
| Base branch | FAIL: origin has no profile base branch (`rk install`) |
| gth-pr-bot | FAIL: a label, the Client ID variable or the key secret is missing, or the variable isn't the user config's Client ID (`rk configure`) |
| Repo settings and rulesets | WARN: a setting or a `repo-kit: …` ruleset is missing or differs from the config, with the same lines as `rk github diff` (`rk github apply`); or the API can't be read |
| Worktrees | WARN: a worktree under `worktree_root` whose PR is merged or closed (`rk wt done`) |

Still to come: the latest release next to the pin, and `gth-release-bot`. Whether the App is installed and its token mint works can't be seen from a local run: confirm
the install at https://github.com/settings/installations and run the update-prs caller with dry-run ticked.

Minimum versions, checked by `doctor`: Git 2.38+ (`git.MIN_GIT`; every `rk wt` command checks it too), gh 2.40+, Python 3.11+.

## 🏷️ Versioning

- repo-kit itself: release-please, immutable `vX.Y.Z` tags, no floating `v1`. It manages its own repo with `rk`:
  the `.gitignore` block, the CONTRIBUTING blocks, `release-please-config.json` and the manifest are rendered and
  checked by `pr-checks / managed-files` like anywhere else. Its callers can't be generated, because their paths are
  the reusable workflows themselves, so `.repo-kit.toml` marks those three `unmanaged` and hand-written callers
  (`pr-checks-self.yml`, `keep-prs-current.yml`, `release-self.yml`) run them locally: every PR tests its own
  workflows. `doctor` shows the three overrides as WARN, as expected.
- **One pin per consumer:** `version` in `.repo-kit.toml`. Every generated caller uses `@v<version>` and passes that
  same version to the engine. Bumping the pin is always `rk upgrade`, so callers, managed files and rulesets move
  together.
- **Dependabot is not used for repo-kit refs.** It would bump `uses:` lines without re-rendering the other files, and
  the drift check would fail. If Dependabot is enabled for other actions, rk adds an `ignore` entry for
  `GreenMachine582/repo-kit`.
- Upgrades are one PR per repo, made with `rk upgrade`. Fanning them out automatically on release is a Future item.
- Breaking changes are `feat!:` with migration notes. `rk upgrade` points to the release notes now and will print the
  notes themselves in Phase 1D.

## Per-repo changes

### photo-ledger

Moves from `main` only to `dev-main` (Phase 2).

1. Finish Phase 0 first: #35 still targets `main`.
2. `rk bootstrap --profile dev-main`:
   - creates `dev` from `main`, and asks before retargeting open PRs to `dev`;
   - sets `dev` as the default branch;
   - creates `.repo-kit.toml`, `.repo-kit.lock`, the caller workflows (`update-prs`, `pr-checks`, `release`),
     `release-please-config.json`, the manifest (seeded from the current `pyproject.toml` version), the `.gitignore`
     block and the four CONTRIBUTING blocks (Branches, with the `dev` diagram; Pull requests; Several PRs at once;
     Releasing).
3. By hand:
   - `next-task` step 5 uses `worktree-start`;
   - `open-pr` targets `dev`, opens drafts, marks them ready, and syncs with `worktree-sync`;
   - `CLAUDE.md` gets a short prose note pointing to `.repo-kit.toml`;
   - delete `.github/workflows/release-please.yml` (the managed `release.yml` replaces it), and set
     `gth-release-bot`'s new variable and secret ([github-app.md](github-app.md#releases-gth-release-bot)).
4. Local clones need `git fetch` and a checkout of `dev`.

### GTH repos

Already `dev-main`; migrating (Phase 3), in the order core → fastapi → ui.

1. `rk sync` takes over each existing workflow once it matches the render; where one differs,
   it refuses with the diff, and the difference is settled by hand (a config value, an `[overrides]` entry, or
   dropping it). The existing manifest is kept (seeded). `scripts/use-local-gth.sh` stays the repo's own until the
   `use-local-gth` extra is built (a Future item).
2. Delete `scripts/apply-rulesets.sh`, `scripts/prune-branches.sh`, `.github/rulesets/*.json`,
   `.github/repo-settings.json` and `.github/workflows/release-please.yml` (the managed `release.yml` replaces it).
   Set `gth-release-bot`'s new variable and secret, and delete the old `RELEASE_APP_*` ones
   ([github-app.md](github-app.md#releases-gth-release-bot)).
3. `rk github diff` must show no unexpected changes before `apply`. After applying, delete the old `dev`, `main`
   and `release tags` rulesets, which the diff lists as left alone.
4. The CONTRIBUTING sections become managed blocks. GTH's Branches, Pull requests, Chained PRs and Releasing
   sections are deleted, and `rk sync` appends the blocks that replace them. Repo-specific sections such as
   *Developing against local GTH repos* and *Rules* stay outside the markers.
5. greentechhub-core's `local_gth.py` gets the new resolution order ([worktrees.md](worktrees.md#-local-gth-mode)).

### Other repos

PyFinBot and the rest (Phase 4): one `rk bootstrap` each. PyFinBot's `release-please-config.json` has an `extra-files`
entry (`src/pyfinbot/version.py`) that the managed config lacks, so `rk sync` refuses it until that is settled (see
the Future item in TODO.md).
