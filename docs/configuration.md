[← Back to README](../README.md)

# ⚙️ Configuration

Two files. Unknown keys in either are errors, and `rk doctor` validates both.

## Repo config: `.repo-kit.toml`

Committed. The single input to everything rk generates or applies. A repo without one gets every default shown
below (`profile = "dev-main"`, no setup or check, `env_dirs = [".venv"]`). `rk install` seeds it with `version`, `profile` and
`[project]` ([adoption.md](adoption.md#install-step-by-step)), and from then on it belongs to the repo (`seeded`
mode: rk never rewrites it, except `rk upgrade` changing `version`).

```toml
version = "1.4.0"                 # the ONE pin; every generated caller uses it
profile = "dev-main"              # the only profile for now (main-only is Future)

[project]
name = "greentechhub-core"
release_type = "python"           # release-please release type

[checks]
required = ["test"]               # this repo's own CI jobs that rulesets must require
pr_title = "not-conventional"     # or "conventional" (for squash-merge repos); also picks the CONTRIBUTING text
commit_header_max = 100

[release]
app = "gth-release-bot"           # the only actor allowed to create v* tags; bypasses dev for the back-merge
back_merge = true                 # merge main back into dev after a release (dev-main only)

[update_prs]
enabled = true
skip_labels = ["no-auto-update", "wip"]
conflict_label = "needs-merge"

[worktree]
setup = ["uv venv", "uv pip install -e .[dev]"]
check = "./scripts/check.sh"
env_dirs = [".venv"]

[extras]
use_local_gth = true
gth_packages = ["greentechhub-core", "greentechhub-fastapi"]

[overrides]                       # escape hatch, used sparingly; doctor lists any in use
# files.".github/workflows/release.yml" = "unmanaged"
```

| Key | Used by |
|---|---|
| `version` | Every generated caller (`@v<version>`), the engine version CI runs, `rk upgrade`. See [adoption.md](adoption.md#-versioning) |
| `profile` | Templates (`templates/profiles/<profile>/`), the default base for `wt start`, rulesets and settings |
| `[project]` | `release-please-config.json`, the manifest |
| `[checks]` | `pr-checks` and the required checks in the rulesets ([checks-and-release.md](checks-and-release.md)) |
| `[release]` | `release.yml`, the tags ruleset |
| `[update_prs]` | `update-prs` ([pr-updates.md](pr-updates.md)) |
| `[worktree]` | `wt start` (`setup`), `wt sync` (`check`), `wt done` (`env_dirs`) ([worktrees.md](worktrees.md)) |
| `[extras]` | Opt-in templates. `use_local_gth` is accepted, but adds nothing until the `use-local-gth` extra is built (a Future item) |
| `[overrides]` | Per-file ownership changes. A file set to `unmanaged` is left alone; `doctor` warns about each one |

## User config: `~/.config/repo-kit/config.toml`

Per machine, never committed. The same path on every OS; set `REPO_KIT_CONFIG` to use another file. Missing means
the defaults.

```toml
worktree_root = "~/dev/wt"

[pr_bot]                          # used by `rk configure` to enrol a repo
client_id = "Iv23..."             # gth-pr-bot's Client ID
private_key = "~/.ssh/gth-pr-bot.pem"
```

`worktree_root` and `pr_bot.private_key` must not be inside OneDrive or any other synced folder: rk refuses one under
a `OneDrive*`, Dropbox or iCloud folder. See [github-app.md](github-app.md#enrolling-a-repo).

## Worktree-local: `.repo-kit.local.toml`

Gitignored, written into a worktree by `rk wt start --link`. It holds the explicit paths of linked GTH packages. See
[worktrees.md](worktrees.md#-local-gth-mode).
