[← Back to README](../README.md)

# 📄 Shared files: `rk sync`

`rk sync` renders the templates (common + profile + enabled extras) with values from `.repo-kit.toml`, writes them
into the repo, and records each one in **`.repo-kit.lock`** (committed).

```
templates/common/                 every repo: callers, .gitignore block, CONTRIBUTING blocks
templates/profiles/dev-main/      dev + main, back-merge
templates/extras/use-local-gth/   opt-in per repo
```

## Layers and manifests

Each layer folder has a `_manifest.toml` naming its files. The key is the path in the consumer repo:

```toml
[files.".github/workflows/update-prs.yml"]
mode = "managed"

[files.".gitignore"]
mode = "block"
block = "local"                   # the block's name in its markers
source = "gitignore.block"        # the template file here, when it isn't the target path
```

Unknown keys are errors, and a test checks that every file in a layer is listed and every entry exists. A path
claimed by two layers is an error. A layer without a manifest has no templates.

## Placeholders

Only `${lowercase_name}` is a placeholder; an unknown one is an error. Everything else stays exactly as written, so
workflow files and shell scripts need no escaping: `${{ github.sha }}`, `$VAR`, `${UPPER}` and `$$`. A shell variable
in a template must therefore be upper case, or written `$name`.

| Placeholder | Value |
|---|---|
| `${version}` | `version`, the one pin |
| `${profile}` | `profile` |
| `${project_name}` | `[project].name`, else the repo name |
| `${release_type}` | `[project].release_type` |
| `${default_base}` | The profile's default branch (`dev`) |
| `${owner}`, `${repo}` | From `origin` |

Anything conditional is a profile directory or built in code.

## Ownership modes

| Mode | Behaviour | Used for |
|---|---|---|
| `managed` | Fully owned by repo-kit; rewritten on every sync. Edited by hand → sync refuses and shows the diff, and the drift check fails | Caller workflows, `release-please-config.json`, `use-local-gth.sh` |
| `block` | Only the text between `# >>> repo-kit: <name>` and `# <<< repo-kit: <name>` markers is owned; the rest of the file is the consumer's. A file without the markers gets the block appended | `.gitignore` lines, `CONTRIBUTING.md` sections (Branches, Pull requests, Releasing, Several PRs at once) |
| `seeded` | Created once if missing, never touched again | `.release-please-manifest.json`, `CHANGELOG.md` stub, `.repo-kit.toml` itself |

In Markdown files the markers sit inside HTML comments, so they don't render.

A file (or block) that isn't in the lock yet is taken over if it already matches the render, and refused with the diff
otherwise. Content is compared with LF line endings, so CRLF from `core.autocrlf` is never mistaken for an edit; rk
writes LF.

## The lock

`.repo-kit.lock` is JSON (the standard library can read TOML but not write it), with sorted keys:

```json
{
  "files": {
    ".github/workflows/update-prs.yml": {"layer": "common", "mode": "managed", "sha256": "…"},
    ".gitignore": {"block": "local", "layer": "common", "mode": "block", "sha256": "…"}
  },
  "version": "1.4.0"
}
```

`sha256` is the hash of what rk last rendered (for a block, of its contents). Content that no longer matches it was
edited by hand. A lock entry whose template has gone is reported and left alone.

## Commands

| Command | Does |
|---|---|
| `rk sync` | Render and write. Prints a per-file summary (created, updated, taken over, unchanged, refused, skipped), then the `Changed:` list |
| `rk sync --check` | No writes. Non-zero if any managed file or block differs from what the pinned version would render, a seeded file is missing, or the lock is out of date. Run in CI by `pr-checks / managed-files` |
| `rk sync --adopt` | **Future item.** For repos that already have these files (GTH). Compares each existing file to the rendered one, shows the differences, and takes ownership only after you confirm. Until then, plain `rk sync` takes over an existing file only if it already matches the render, and refuses with the diff otherwise. A difference worth keeping becomes either a config value or an `[overrides]` entry |

`version` must be set: callers pin `@v${version}`.

## Rules

- **All or nothing:** the whole sync is planned first; if any file is refused, nothing is written.
- Never write outside the lock's known paths and new template paths.
- Never touch `seeded` files after creation.
- Refuse (don't merge) when a `managed` file has local edits. The fix is to move the change upstream into repo-kit,
  or to mark the file `unmanaged` in `[overrides]`, which skips it and which `doctor` flags.
- Every file rk replaces is first copied to `.git/repo-kit/backup/<UTC time>/`, outside the working tree; the
  `Changed:` list names the folder.
- An upgrade that adds a template adds the file and updates the lock. Renamed and removed templates are a Future
  item: until then the migration notes say which file to move or delete by hand.
