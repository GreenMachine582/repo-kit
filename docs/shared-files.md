[← Back to README](../README.md)

# 📄 Shared files: `rk sync`

`rk sync` renders the templates (common + profile + enabled extras) with values from `.repo-kit.toml`, writes them
into the repo, and records each one in **`.repo-kit.lock`** (committed): path, mode, template version and a hash of
the rendered output.

```
templates/common/                 every repo: callers, .gitignore block, CONTRIBUTING blocks
templates/profiles/dev-main/      dev + main, back-merge
templates/extras/use-local-gth/   opt-in per repo
```

Templates use only `${var}` placeholders (`string.Template`). Anything conditional is a profile directory or built in
code.

## Ownership modes

| Mode | Behaviour | Used for |
|---|---|---|
| `managed` | Fully owned by repo-kit; rewritten on every sync. Edited by hand → sync refuses and shows the diff, and the drift check fails | Caller workflows, `release-please-config.json`, `use-local-gth.sh` |
| `block` | Only the text between `# >>> repo-kit: <name>` and `# <<< repo-kit: <name>` markers is owned; the rest of the file is the consumer's | `.gitignore` lines, `CONTRIBUTING.md` sections (Branches, Pull requests, Releasing, Several PRs at once) |
| `seeded` | Created once if missing, never touched again | `.release-please-manifest.json`, `CHANGELOG.md` stub, `.repo-kit.toml` itself |

In Markdown files the markers sit inside HTML comments, so they don't render.

## Commands

| Command | Does |
|---|---|
| `rk sync` | Render and write. Prints a per-file summary: created, updated, unchanged or refused |
| `rk sync --check` | No writes. Non-zero if any managed file or block differs from what the pinned version would render. Run in CI by `pr-checks / managed-files` |
| `rk sync --adopt` | **Future item.** For repos that already have these files (GTH). Compares each existing file to the rendered one, shows the differences, and takes ownership only after you confirm. Until then, plain `rk sync` takes over an existing file only if it already matches the render, and refuses with the diff otherwise. A difference worth keeping becomes either a config value or an `[overrides]` entry |

## Rules

- Never write outside the lock's known paths and new template paths.
- Never touch `seeded` files after creation.
- Refuse (don't merge) when a `managed` file has local edits. The fix is to move the change upstream into repo-kit,
  or to mark the file `unmanaged` in `[overrides]`, which `doctor` flags.
- An upgrade that adds a template adds the file and updates the lock. Renamed and removed templates are a Future
  item: until then the migration notes say which file to move or delete by hand.
