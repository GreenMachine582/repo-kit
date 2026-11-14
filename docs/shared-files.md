[← Back to README](../README.md)

# 📄 Shared files: `rk sync`

`rk sync` renders the templates (common + profile + enabled extras) with values from `.repo-kit.toml`, writes them
into the repo, and records each one in **`.repo-kit.lock`** (committed).

```
templates/common/                 every repo: callers, .gitignore block
templates/profiles/dev-main/      dev + main, back-merge, the CONTRIBUTING blocks
templates/extras/use-local-gth/   opt-in per repo (Future: no templates yet)
```

## Layers and manifests

Each layer folder has a `_manifest.toml` naming its files. The key is the path in the consumer repo:

```toml
[files.".github/workflows/update-prs.yml"]
mode = "managed"

[files."release-please-config.json"]
mode = "managed"
source = "release-please.json"    # the template file here, when it isn't the target path

# in profiles/dev-main/_manifest.toml
[files."CONTRIBUTING.md"]
mode = "block"                    # each block: its name in the markers = its template file here
blocks = { branches = "contributing/branches.md", pull-requests = "contributing/pull-requests.md", chained-prs = "contributing/chained-prs.md", releasing = "contributing/releasing.md" }
```

A block file lists every block it holds, in the order they are appended to a file that has none of them yet. Unknown
keys are errors, and a test checks that every file in a layer is listed and every entry exists. A layer without a
manifest has no templates.

Two layers may name the same path only when both add blocks to it and no block name repeats, so a profile can add
its own section to a file `common` already has blocks in. Any other path claimed by two layers is an error.

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
| `${release_branch}` | The profile's release branch (`main`) |
| `${back_merge_into}` | `${default_base}` when `[release].back_merge` is true, else empty |
| `${commit_header_max}` | `[checks].commit_header_max` |
| `${pr_title_rule}` | The PR-title paragraph for `[checks].pr_title`, one of two fixed texts built in code (`files.PR_TITLE_TEXT`) |
| `${owner}`, `${repo}` | From `origin` |

Anything conditional is a profile directory or built in code.

## Ownership modes

| Mode | Behaviour | Used for |
|---|---|---|
| `managed` | Fully owned by repo-kit; rewritten on every sync. Edited by hand → sync refuses and shows the diff, and the drift check fails | Caller workflows, `release-please-config.json` (and `use-local-gth.sh`, a Future item) |
| `block` | Only the text between `# >>> repo-kit: <name>` and `# <<< repo-kit: <name>` markers is owned; the rest of the file is the consumer's. A file without the markers gets the block appended | `.gitignore` lines, `CONTRIBUTING.md` sections (Branches, Pull requests, Several PRs at once, Releasing) |
| `seeded` | Created once if missing, never touched again | `.release-please-manifest.json`, `CHANGELOG.md` stub, `.repo-kit.toml` itself |

In Markdown files the markers sit inside HTML comments, so they don't render.

### The CONTRIBUTING blocks

The dev-main profile owns four sections of `CONTRIBUTING.md`, each with its `##` heading: `branches` (the flow and
branch names), `pull-requests` (drafts, commits, PR titles, updating a PR), `chained-prs` (Several PRs at once) and
`releasing`. They live in the profile, not `common`, because each describes the dev-main branch model. A repo
without them gets all four appended in that order; after that they can be moved around the file. Everything outside
the markers (an intro, local setup, repo-specific rules) is the repo's own.

A file (or block) that isn't in the lock yet is taken over if it already matches the render, and refused with the diff
otherwise. Content is compared with LF line endings, so CRLF from `core.autocrlf` is never mistaken for an edit; rk
writes LF.

## The lock

`.repo-kit.lock` is JSON (the standard library can read TOML but not write it), with sorted keys:

```json
{
  "files": {
    ".github/workflows/update-prs.yml": {"layer": "common", "mode": "managed", "sha256": "…"},
    ".gitignore": {"blocks": {"local": {"layer": "common", "sha256": "…"}}, "mode": "block"}
  },
  "version": "1.4.0"
}
```

`sha256` is the hash of what rk last rendered (for a block, of its contents). Content that no longer matches it was
edited by hand. Each block is recorded and compared on its own, but a file is written whole: one refused block
refuses the file. A lock entry whose template has gone is reported and left alone.

## Commands

| Command | Does |
|---|---|
| `rk sync` | Render and write. Prints a per-file summary (created, updated, taken over, unchanged, refused, skipped), then the `Changed:` list |
| `rk sync --check` | No writes. Non-zero if any managed file or block differs from what the pinned version would render, a seeded file is missing, or the lock is out of date. Prints the diff of every file that differs. Run in CI by `pr-checks / managed-files` |
| `rk sync --adopt` | **Future item.** For repos that already have these files (GTH). Compares each existing file to the rendered one, shows the differences, and takes ownership only after you confirm. Until then, plain `rk sync` takes over an existing file only if it already matches the render, and refuses with the diff otherwise. A difference worth keeping becomes either a config value or an `[overrides]` entry |

`version` must be set: callers pin `@v${version}`. It must also be the version of the rk that is running, because the
templates come with the engine: with another release installed, `rk sync` refuses and names both versions, rather
than write files that CI's `--check` (which runs the pinned engine) would reject. Move the pin with `rk upgrade`, or
install the pinned rk. An unreleased engine (version `0.0.0`: a source checkout, or CI running rk from the tag it
checked out) has nothing to compare and is let through.

## Rules

- **All or nothing:** the whole sync is planned first; if any file is refused, nothing is written.
- Never write outside the lock's known paths and new template paths. A target that a symlink would take outside the
  repo, that is a directory, or that isn't UTF-8 text is refused.
- Never touch `seeded` files after creation. `.repo-kit.toml` is one: `rk install` builds the first config and
  the sync writes it from `templates/common/repo-kit.toml`. `rk upgrade` is the only writer of it afterwards, and
  only of its `version` line ([adoption.md](adoption.md#upgrade-step-by-step)).
- `.claude/settings.json` is the one file rk edits without owning any of it: `rk install` adds two keys where
  they are missing ([adoption.md](adoption.md#install-step-by-step)), and it isn't in the lock.
- Refuse (don't merge) when a `managed` file has local edits. The fix is to move the change upstream into repo-kit,
  or to mark the file `unmanaged` in `[overrides]`, which skips it and which `doctor` flags.
- Every file rk replaces is first copied to `.git/repo-kit/backup/<UTC time>/`, outside the working tree; the
  `Changed:` list names the folder.
- An upgrade that adds a template adds the file and updates the lock. Renamed and removed templates are a Future
  item: until then the migration notes say which file to move or delete by hand.
