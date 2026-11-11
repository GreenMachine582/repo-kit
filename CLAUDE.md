# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

`repo-kit` (CLI `rk`, package `repo_kit`): shared tooling for GreenMachine582's repos. It gives each Claude session its
own worktree, keeps open PRs current with their base, and owns the files, checks, release flow, rulesets and settings
every repo shares. Design details are in [docs/](docs/); open work and phases are in [TODO.md](TODO.md).

## Hard rules

1. **Standard library only at runtime.** `dependencies = []` stays empty. Python 3.11+: `tomllib`, `string.Template`,
   `json`, `pathlib`, `subprocess`, `argparse`. Dev tools (pytest, ruff) go in the `dev` extra only.
2. **Merge only.** Nothing in rk rebases, or pushes with `--force` or `--force-with-lease`. The bot never merges a PR.
3. **Never write a consumer's version or `CHANGELOG.md`.** release-please owns both. `seeded` files are never touched
   after creation.
4. **Never write outside the lock's known paths and new template paths**, and refuse (don't merge) when a `managed`
   file has local edits.
5. **`rk github apply` never runs in CI**, and always shows the diff and asks first. `diff` is the default.
6. **Every write is safe to re-run**: it checks tool versions and the remote, backs up what it replaces, and ends with
   a list of exactly what changed.
7. **`wt done` overrides (`--allow-unmerged`, `--discard-changes`, `--delete-unpushed`) are never used unasked**, by
   rk's own skills or by you.

## Design rules

- Call, don't copy: CI logic lives in reusable workflows, scripts become `rk` commands, and only thin callers and
  config are written into consumers. See [docs/architecture.md](docs/architecture.md).
- Templates use only `${var}` placeholders. Anything conditional is a profile directory or built in code. Rulesets are
  Python dicts, never templated JSON. No Jinja.
- Reconcile state, don't replay events: `update-prs` looks at every open PR each run.
- Always use a PR's live `base.ref`, never an assumed `dev`.
- Paths go through `pathlib`; subprocesses take argument lists, never a shell string. Everything must work on Windows.
- Everything that talks to GitHub goes through `github.py`, so scenario tests can swap in a fake PR store.
- Unknown config keys are errors.
- The commit and title rules live once, in `checks.py`, used by both CI and the local hook.

## Commands

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
.venv/bin/pytest
.venv/bin/rk --help
```

`.venv` belongs to one machine: build it with uv on each device, and never keep it in a synced folder such as
OneDrive. On Windows the tools are in `.venv/Scripts/`.

## Conventions

- `src/` layout. Templates live in `templates/` and ship inside the wheel as `repo_kit/templates`.
- Tests: `tests/unit/` for pure logic, `tests/scenarios/` for Git and file sync scenarios against a bare origin and
  clones in `tmp_path`. Tests never touch a real repo or GitHub.
- Commits are Conventional Commits. **PR titles are not** (merge-commit model). See [CONTRIBUTING.md](CONTRIBUTING.md).
- A new command is declared in `cli.py` with its final arguments first, then built in its own module.
- Docs change in the same PR as the behaviour they describe. [TODO.md](TODO.md) holds open work only.
