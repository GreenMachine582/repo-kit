# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

`repo-kit` (CLI `rk`, package `repo_kit`): shared tooling for GreenMachine582's repos. It gives each Claude session its
own worktree, keeps open PRs current with their base, and owns the files, checks, release flow, rulesets and settings
every repo shares. Design details are in [docs/](docs/); open work and phases are in [TODO.md](TODO.md).

## Hard rules

1. **Standard library only at runtime.** `dependencies = []` stays empty. Python 3.12+: `tomllib`, `string.Template`,
   `json`, `pathlib`, `subprocess`, `argparse`. Dev tools (pytest, ruff) go in the `dev` extra only.
2. **Merge only.** Nothing in rk rebases, or pushes with `--force` or `--force-with-lease`. The bot never merges a PR.
3. **Never write a consumer's version or `CHANGELOG.md`.** release-please owns both. `seeded` files are never touched
   after creation, with one exception: `rk upgrade` moves the `version` line of `.repo-kit.toml`, and nothing else.
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

`rk configure`, `rk bootstrap` and `rk secrets` also call the `sops` (3.10+) and `age-keygen` binaries, like `gh`.
They're not Python dependencies, so hard rule 1 holds. Tests use `FakeSops`; one round trip runs real sops when
it's installed.

## Agent workflow

For every Claude session or subagent working on a branch here:

- **Worktree:** start with `rk wt start <type/desc> --no-setup` from the main checkout, then work only in the
  printed worktree. Build its venv with `uv venv` and `uv pip install --python .venv/Scripts/python.exe -e ".[dev]"`.
  A plain `uv pip install` follows `VIRTUAL_ENV`, which may point at the main checkout, and would repoint that
  checkout's `rk`. Run the install again after merging a `dev` that changed the `dev` extra.
- **Scratch files:** agents running in parallel share one scratch folder, so name temporary files after the branch
  (`msg-feat-x.txt`, not `msg.txt`).
- **Editing:** use the editor tools, not shell heredocs, for text with backslashes: the shell here turns `\n` and `\a`
  into real characters.
- **Before each commit:** `ruff check`, `ruff format --check`, `mypy`, `pytest` and `rk sync --check` must pass.
  Check exit codes directly: `cmd | tail && git commit` hides a failure behind the pipe. This machine is Windows,
  and CI only runs the Windows tests on release PRs and merges to `dev`, so this local `pytest` is the Windows check
  for a PR into `dev`. It runs in parallel (`-n0` for one process), and test file basenames must be unique across
  `tests/unit` and `tests/scenarios`.
- **Commit and PR:** check commits with `rk check commits origin/dev..HEAD`. Open a draft PR to `dev`, read the check
  results, and mark it ready (`gh pr ready`) once CI is green. The user merges.
- **TODO.md:** agents working in parallel leave it alone. The lead updates it once, after the PRs merge.
- **Credentials:** never read the age key, `.pem` files, the user config or decrypted credentials. Never run
  `rk github apply`, `rk configure` or `rk secrets` against real repos. Tests use `tmp_path` and a fake home folder.

## Conventions

- `src/` layout. Templates live in `templates/` and ship inside the wheel as `repo_kit/templates`.
- Tests: `tests/unit/` for pure logic, `tests/scenarios/` for Git and file sync scenarios against a bare origin and
  clones in `tmp_path`. Tests never touch a real repo or GitHub.
- Commits are Conventional Commits. **PR titles are not** (merge-commit model). See [CONTRIBUTING.md](CONTRIBUTING.md).
- A new command is declared in `cli.py` with its final arguments first, then built in its own module.
- Docs change in the same PR as the behaviour they describe. [TODO.md](TODO.md) holds open work only.
- repo-kit manages its own files (`.repo-kit.toml`). A PR that changes `templates/` also runs `rk sync` and commits
  the result, or `pr-checks / managed-files` fails.
