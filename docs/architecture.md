[← Back to README](../README.md)

# 🏗️ Architecture

```
                         rk (Python engine, stdlib only)
          /                        |                              \
  Worktree side (local)     PR side (Actions)             Repo setup side
  wt start / sync / done    update-prs reconcile          sync (files) · github apply (rulesets, settings)
  plugin skills call it     reusable workflows call it    doctor · install · upgrade
```

One engine serves three sides. Claude sessions reach it through the plugin's skills, CI through reusable workflows,
and people through the `rk` command.

## Call, don't copy

Shared logic goes to the cheapest place that keeps one copy:

| Kind of thing | Where it lives | What the consumer holds |
|---|---|---|
| CI logic (checks, release, PR updates) | Reusable workflows in repo-kit | A thin caller workflow, generated |
| Scripts (`apply-rulesets.sh`, `prune-branches.sh`) | `rk` commands | Nothing |
| GitHub-side config (rulesets, repo settings) | Built by `rk` from the profile and config, applied by API | Nothing; drift reported by `doctor` |
| Files that must physically be in the repo (`release-please-config.json`, `.gitignore` lines, CONTRIBUTING sections; `use-local-gth.sh` is a Future item) | Templates in repo-kit | The rendered file, tracked in `.repo-kit.lock` |
| Repo-specific state (`CHANGELOG.md`, release manifest, app code) | The consumer | Never touched after creation |

## Engine

Python, standard library only, 3.11+:

| Need | Module |
|---|---|
| Config | `tomllib` |
| Placeholders | `string.Template` (`${var}` only) |
| Rulesets and settings | `json`, built from Python dicts |
| Windows-safe paths and processes | `pathlib`, `subprocess` with argument lists |
| CLI | `argparse` |
| Scenario tests | pytest (dev only) |

Anything conditional in a template is handled by **profile directories** (`templates/profiles/dev-main/…`) or built
in code. No Jinja and no extra dependency.

## Distribution

| Where | How |
|---|---|
| Claude plugin | Skills only. They call the `rk` on `PATH` (the standalone install below), so the engine exists once per machine |
| Standalone | `uv tool install git+https://github.com/GreenMachine582/repo-kit@vX.Y.Z` puts `rk` on your `PATH` |
| CI | Reusable workflows check out repo-kit at the version the caller passes in |

## Layout

```
repo-kit/
├── .github/workflows/
│   ├── update-prs.yml            # reusable: keep PRs current                       (Phase 1B)
│   ├── pr-checks.yml             # reusable: commits, pr-title, managed-files drift (Phase 1D)
│   ├── pr-checks-self.yml        # this repo's own caller of pr-checks, until dogfood
│   ├── release.yml               # reusable: release-please + back-merge            (Phase 1D)
│   └── ci.yml                    # this repo's own CI; rk-managed from Phase 1D (dogfood)
├── .claude-plugin/marketplace.json
├── plugins/repo-kit/
│   ├── .claude-plugin/plugin.json
│   └── skills/{worktree-start,worktree-sync,worktree-done}/SKILL.md
├── src/repo_kit/                 # see the module table
├── templates/
│   ├── common/                   # every repo: callers, .gitignore block
│   ├── profiles/dev-main/        # dev + main, back-merge, the CONTRIBUTING blocks (every repo)
│   └── extras/use-local-gth/     # opt-in per repo (Future: no templates yet)
├── tests/{unit,scenarios}/
├── docs/
├── pyproject.toml  CHANGELOG.md  README.md
└── .repo-kit.toml                # rk manages its own repo too (Phase 1D)
```

| Module | Responsibility | Phase |
|---|---|---|
| `cli.py` | The `rk` command tree | 1A |
| `config.py` | `.repo-kit.toml` and user config | 1A |
| `git.py` | `git` subprocess wrappers | 1A |
| `github.py` | PRs, labels, comments, rulesets, settings through `gh api`; faked in scenarios | 1A, 1B |
| `worktree.py` | `wt start / sync / done` | 1A |
| `reconcile.py` | `update-prs` | 1B |
| `files.py` | `sync`: render, lock, ownership modes, drift | 1C |
| `bootstrap.py` | `install`, `configure`, `bootstrap`, `upgrade` | 1C |
| `doctor.py` | Read-only health check | 1C (rulesets and settings: 1D) |
| `checks.py` | Commit and PR title rules | 1D |
| `rulesets.py`, `settings.py` | Build the rulesets and repo settings, and diff them against GitHub | 1D |
| `github_config.py` | `github diff / apply`: read, diff, back up, apply | 1D |
| `branches.py` | `branches prune` (replaces `prune-branches.sh`) | 1D |

## Constraints carried over

These come from GTH's `CONTRIBUTING.md` and shape everything rk does:

| Existing rule | Consequence |
|---|---|
| Merge commits only; don't rewrite a branch others depend on | The bot and `wt sync` merge only. Rebase is a Future item |
| Every commit conventional; PR title must **not** be conventional (merge-commit model) | `pr-checks` enforces both, with the title rule set per repo |
| PR base is `dev`, a feature branch (chains), or `main` for release PRs | Always use the PR's live `base.ref`; the branch model is a per-repo **profile** (`dev-main` for every repo for now) |
| `v*` tags never move; only `gth-release-bot` creates them | Exact `vX.Y.Z` pins; the tag ruleset names that App as the only creator |
| Admin can merge past failing checks (logged); nobody pushes straight to `dev`/`main` | Kept as-is in the generated rulesets |
| CI and production never use local GTH mode | Worktree cleanup undoes links |
| release-please owns version bumps and `CHANGELOG.md` | rk never writes either (ownership modes) |

## Key decisions

| Decision | Choice | Why | Rejected |
|---|---|---|---|
| Bot identity | GitHub App | Its pushes trigger CI, and its permissions are scoped per repo | PAT: tied to a person, broad scope |
| Which App | A separate `gth-pr-bot` | Keeps release powers (tags) apart from PR housekeeping | Reusing `gth-release-bot` |
| Worktree root | `~/dev/wt`, per-machine config | Never OneDrive: venvs and Git internals don't survive syncing | Inside the repo, or a synced folder |
| Worktree engine | Own engine, thin skills | Same behaviour for people, Claude and CI; branch naming, setup and links in one place | Claude's built-in worktrees |
| Shared rulesets and checks | In scope, applied by API from one definition | The copies were drifting; one definition makes check names and rulesets match | Copying JSON and scripts into each repo |
| Updating a branch | Merge only | Branches can be shared and chained; history is never rewritten | Rebase (Future, private branches only) |
| Conflicts | Flagged once, resolved locally | A wrong automatic resolution is worse than a label | Auto-resolve (Future: local first, CI `suggest` only) |
| Config contract | `.repo-kit.toml` + per-machine user config | One committed input for everything generated or applied | Settings spread across workflows and scripts |
| Work in progress | Drafts, plus `wip` for ready PRs under rework | Uses GitHub's own signal; no bot races with an active session | A label only |
| Scheduled runs | None for now | Every run reconciles everything, so a missed event is caught by the next one | A daily safety net (Future) |
| Version pinning | One `version` in config; `rk upgrade` | Callers, managed files and rulesets move together | Dependabot bumping `uses:` lines, which breaks the drift check |
| Engine language | Python, stdlib only (3.11+) | Windows-safe, testable, nothing to install beyond Python | Bash: quoting and Windows pain. Extra dependencies: harder to bundle |
| Branch model | `dev-main` is the only profile built; photo-ledger moves to it | One profile to test; every repo ends up the same | Building `main-only` now (Future) |
| Name | `repo-kit`: CLI `rk`, package `repo_kit`, config `.repo-kit.toml`, lock `.repo-kit.lock`, markers `# >>> repo-kit: <name>` | Short, describes the scope beyond workflows | `dev-workflows` |

## Risks

| Risk | Mitigation |
|---|---|
| A generated ruleset locks you out or blocks merges | `diff` is the default; `apply` asks; admin pull-request bypass kept; live check in the pilot |
| Required-check names drift from workflow job names | Both generated from one place; the pilot confirms |
| Adoption overwrites GTH's tuned files | `sync` takes over an existing file only if it already matches, and refuses with the diff otherwise; manifest and CHANGELOG are seeded or consumer-owned |
| Managed files edited by hand go unnoticed | `managed-files` check in every PR |
| The bot races an active session | Drafts skipped; non-force pushes; `wt sync` catches up |
| Dropped workflow events | Full reconcile every run |
| An agent discards work during cleanup | Separate overrides per unsafe state; the skill forbids unasked use |
| Windows quirks | Python engine; Windows CI job; `doctor` checks |
| Scope creep | Future items start only after Phase 4 |
