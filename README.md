# 🧰 repo-kit

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg?logo=opensourceinitiative&logoColor=white)](LICENSE)
[![Status: Pre-alpha](https://img.shields.io/badge/Status-Pre--alpha-orange.svg)](TODO.md)
[![Python](https://img.shields.io/badge/Python-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-DE5FE9.svg?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-2088FF.svg?logo=githubactions&logoColor=white)](https://docs.github.com/actions)
[![pytest](https://img.shields.io/badge/pytest-0A9EDC.svg?logo=pytest&logoColor=white)](https://docs.pytest.org/)

## 🎯 Objective

Several Claude sessions can work independently across several repos, produce chained PRs, and keep those PRs current
without manual Git maintenance, on top of one shared, versioned definition of how every repo is set up.

It fixes three problems:

- Several Claude sessions against one repo trample each other's branch, `.venv` and working tree.
- Open PRs drift behind their base, and conflicts surface late.
- Each repo carries its own copy of the same files: commit and PR title checks, the release-please workflow and
  config, rulesets JSON, `apply-rulesets.sh`, `prune-branches.sh`, `use-local-gth.sh`. Every improvement is copied by
  hand, and the copies drift.

> [!NOTE]
> **Status: pre-alpha.** The worktree commands (`rk wt start / sync / done`), `rk update-prs` and the plugin's skills
> work, and the `update-prs` reusable workflow keeps PRs current. Every other command says which phase delivers it.
> Next is Phase 1C (shared files and adoption). See [TODO.md](TODO.md).

## 🧩 Scope

| Side | What it gives you | Commands |
|---|---|---|
| Worktrees (local) | **One branch = one worktree = one Claude session**, each with its own environment, outside OneDrive | `rk wt start / sync / done`, plugin skills |
| PRs (Actions) | **PRs that stay current on their own.** Ready PRs get their base merged in; real conflicts are flagged once | `update-prs` reusable workflow |
| Repo setup | **One source of truth** for CI checks, the release flow, rulesets, settings and common files. Adopt with one command, upgrade by changing one version, drift detected | `rk sync`, `rk github`, `rk install / upgrade / doctor` |

**Not goals:** a monorepo; replacing release-please; rewriting shared history; the bot merging PRs; a general-purpose
template engine.

## 🚀 Quick start

Install `rk` on your `PATH`, pinned to a release:

```bash
uv tool install git+https://github.com/GreenMachine582/repo-kit@vX.Y.Z
```

Then, in a repo you want to adopt:

```bash
rk bootstrap --profile dev-main   # install + configure + doctor
rk wt start feat/my-change         # a branch in its own worktree, with its own venv
```

In Claude Code, `rk install` adds the plugin marketplace to the repo's `.claude/settings.json`. The plugin holds only
the skills `worktree-start`, `worktree-sync` and `worktree-done`: they call the `rk` on your `PATH`, so there is one
copy of the engine per machine, at the version you installed.

### Developing repo-kit

On Linux and macOS:

```bash
uv venv
uv pip install -e ".[dev]"
.venv/bin/rk --help
```

On Windows, in PowerShell, the venv's tools are in `.venv\Scripts`:

```powershell
uv venv
uv pip install -e ".[dev]"
.venv\Scripts\rk --help
```

## ⌨️ Commands

| Command | Does |
|---|---|
| `rk wt start <type/desc>` | Branch off the base (or `--from`), add the worktree, run setup, apply `--link`s |
| `rk wt sync [branch]` | Merge `origin/<branch>`, then the PR base; check; push without force |
| `rk wt done [branch]` | Remove a finished worktree, refusing every unsafe state |
| `rk update-prs` | Reconcile every open PR against its base (run by the reusable workflow) |
| `rk sync [--check \| --adopt]` | Render managed files and blocks; check drift; take over existing files |
| `rk github diff \| apply` | Compare or apply rulesets and repo settings |
| `rk branches prune [--apply]` | List or delete branches whose PR is merged |
| `rk check commits \| commit-msg \| pr-title` | The commit and title rules `pr-checks` runs |
| `rk install / configure / bootstrap` | Adopt a repo |
| `rk upgrade [vX.Y.Z]` | Move the one version pin and re-render everything |
| `rk doctor` | Read-only health check |

## 📚 Docs

| Doc | Covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 🏗️ Components, "call, don't copy", layout, constraints, decisions and risks |
| [docs/configuration.md](docs/configuration.md) | ⚙️ `.repo-kit.toml` and the per-machine user config |
| [docs/worktrees.md](docs/worktrees.md) | 🌳 `rk wt start / sync / done` and local GTH mode |
| [docs/pr-updates.md](docs/pr-updates.md) | 🔄 The `update-prs` workflow that keeps PRs current |
| [docs/github-app.md](docs/github-app.md) | 🤖 `gth-pr-bot` and `gth-release-bot` |
| [docs/shared-files.md](docs/shared-files.md) | 📄 `rk sync`, ownership modes and the lock file |
| [docs/checks-and-release.md](docs/checks-and-release.md) | ✅ `pr-checks` and `release` reusable workflows |
| [docs/rulesets.md](docs/rulesets.md) | 🔒 Rulesets, repo settings and branch pruning |
| [docs/adoption.md](docs/adoption.md) | 📦 Install, upgrade, versioning and per-repo changes |
| [docs/testing.md](docs/testing.md) | 🧪 Unit tests, Git and file sync scenarios, pilot checks |
| [docs/troubleshooting.md](docs/troubleshooting.md) | 🩺 What to do when `rk` refuses or reports drift |

## 🗺️ Status & Roadmap

Open work, phases and the future list are in [TODO.md](TODO.md). Shipped versions are in `CHANGELOG.md` and on the
[Releases page](https://github.com/GreenMachine582/repo-kit/releases). Branches, PRs and releases are in
[CONTRIBUTING.md](CONTRIBUTING.md).

## 📄 Licence

[MIT](LICENSE) © 2026 Matthew Johnson
