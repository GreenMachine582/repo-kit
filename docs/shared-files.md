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
| `${current_version}` | The version in `pyproject.toml` or `package.json`, else `0.0.0`. Read from the repo by `rk sync`, for the seeded release manifest only |
| `${default_base}` | The profile's default branch (`dev`) |
| `${release_branch}` | The profile's release branch (`main`) |
| `${back_merge_into}` | `${default_base}` when `[release].back_merge` is true, else empty |
| `${conflict_label}` | `[update_prs].conflict_label`, as a quoted JSON string so any label is valid YAML (`"needs-merge"`) |
| `${commit_header_max}` | `[checks].commit_header_max` |
| `${pr_title_rule}` | The PR-title paragraph for `[checks].pr_title`, one of two fixed texts built in code (`files.PR_TITLE_TEXT`) |
| `${owner}`, `${repo}` | From `origin` |

Anything conditional is a profile directory or built in code.

## Ownership modes

| Mode | Behaviour | Used for |
|---|---|---|
| `managed` | Fully owned by repo-kit; rewritten on every sync. Edited by hand → sync refuses and shows the diff, and the drift check fails | Caller workflows, `release-please-config.json` (and `use-local-gth.sh`, a Future item) |
| `block` | Only the text between `# >>> repo-kit: <name>` and `# <<< repo-kit: <name>` markers is owned; the rest of the file is the consumer's. A file without the markers gets the block appended | `.gitignore` lines, `CONTRIBUTING.md` sections (Branches, Pull requests, Several PRs at once, Releasing) |
| `seeded` | Created once, never touched again: a seeded file the repo later deletes stays deleted (the lock records that it was created), and `--check` doesn't mind | `.release-please-manifest.json`, `.repo-kit.toml` itself. Never `CHANGELOG.md`: release-please creates it |

In Markdown files the markers sit inside HTML comments, so they don't render.

### The CONTRIBUTING blocks

The dev-main profile owns four sections of `CONTRIBUTING.md`, each with its `##` heading: `branches` (the flow and
branch names), `pull-requests` (drafts, commits, PR titles, updating a PR), `chained-prs` (Several PRs at once) and
`releasing`. They live in the profile, not `common`, because each describes the dev-main branch model. A repo
without them gets all four appended in that order; after that they can be moved around the file. Everything outside
the markers (an intro, local setup, repo-specific rules) is the repo's own.

A file (or block) that isn't in the lock yet is taken over if it already matches the render, and refused with the diff
otherwise. Content is compared with LF line endings, so CRLF from `core.autocrlf` is never mistaken for an edit. A new file is written with LF; an existing file keeps its own line endings, so rk
writing a block into a CRLF file doesn't convert the rest of it.

## The lock

`.repo-kit.lock` is JSON (the standard library can read TOML but not write it), with sorted keys:

```json
{
  "files": {
    ".github/workflows/update-prs.yml": {"layer": "common", "mode": "managed", "sha256": "…"},
    ".gitignore": {"blocks": {"local": {"layer": "common", "sha256": "…"}}, "mode": "block"}
  },
  "ref_sha": "<40-character commit of v1.4.0>",
  "ref_sha_version": "1.4.0",
  "version": "1.4.0"
}
```

`sha256` is the hash of what rk last rendered (for a block, of its contents). Content that no longer matches it was
edited by hand. Each block is recorded and compared on its own, but a file is written whole: one refused block
refuses the file.

## Removed and renamed templates

A lock entry whose template is no longer in the layers is handled by its recorded mode, on `rk sync` and so on
`rk upgrade`:

| Mode | What happens |
|---|---|
| `managed` | The file is deleted (after a backup) if it still matches the lock. With local edits it is **refused** like any edited managed file, and the lock entry stays until you delete it or restore it, then re-run. A file already gone is just dropped from the lock |
| `block` | Each recorded block is stripped (with its markers and the blank line before it) if it still matches the lock, and the rest of the file is kept. An edited block refuses the file |
| `seeded` | Never touched. The entry is dropped from the lock and the summary says the file is yours |

A removal appears in the summary and under `Changed:` (`removed <path> (...)`, or `<path>: dropped from the lock (...)`
when only the lock changes), and a re-run finds nothing to do. `--check` fails until it has been done. An `unmanaged`
override keeps the old behaviour: reported as "not a template" and left.

A rename is never guessed from matching content: it is declared in the manifest of the layer that holds the new
template, by old path, with no release key because the table limits itself (it only acts on a repo whose lock still
lists the old path):

```toml
[renames]
".github/workflows/old-name.yml" = ".github/workflows/new-name.yml"
```

The old path must not be a template and the new one must be. A declared rename is a removal of the old path, by the
rules above, plus the new template planned like any other, so a hand-edited old file refuses the sync instead of
being moved. For a `seeded` file, the old file is the repo's and stays; the new template is created as a fresh seed.
Without a declaration a rename is just a removal and an addition.

The recorded `mode` is compared too. When a release changes a file's mode, `rk sync` re-records it, notes
`mode changed from <old>` in the summary and lists it under `Changed:`, and `--check` fails until the lock is
updated. A hash recorded under another mode isn't trusted: a file that becomes `managed` is taken over if it already
matches the render, and refused with the diff otherwise, as if it weren't in the lock. A file that becomes `seeded` is
left as it is. A lock entry with no `mode` (an older lock) is read as unchanged.

## Commands

| Command | Does |
|---|---|
| `rk sync` | Render and write. Prints a per-file summary (created, updated, taken over, unchanged, refused, skipped), then the `Changed:` list |
| `rk sync --check` | No writes. Non-zero if any managed file or block differs from what the pinned version would render, a seeded file was never created, or the lock is out of date. Prints the diff of every file that differs. Run in CI by `pr-checks / managed-files` |
| `rk sync --adopt` | For repos that already have these files (GTH). Offers each file that plain `rk sync` would refuse because it differs from the render, or has a hand-edited block: it shows the unified diff, then asks. Only a typed `y` takes the file over: the file is backed up, replaced by the render, and recorded in the lock (listed under `Changed:` as "taken over"). Anything else, and any run with no terminal to answer on, declines it: the file is left as it is and listed as "declined". Other files sync as usual, but a refusal that adoption can't settle (a directory, a non-text file, an edited file of a removed template) still stops the sync. Seeded files and `[overrides]` entries are never offered. A re-run finds nothing to adopt. A difference worth keeping becomes a config value or an `[overrides]` entry instead. Not combinable with `--check` |

`version` must be set: callers pin repo-kit by commit and tag (below). It must also be the version of the rk that is running, because the
templates come with the engine: with another release installed, `rk sync` refuses and names both versions, rather
than write files that CI's `--check` (which runs the pinned engine) would reject. Move the pin with `rk upgrade`, or
install the pinned rk. An unreleased engine (version `0.0.0`: a source checkout, or CI running rk from the tag it
checked out) has nothing to compare and is let through.

## Pinning callers by commit

A generated caller names repo-kit like this, so a moved or re-created tag can't change what runs:

```yaml
uses: GreenMachine582/repo-kit/.github/workflows/pr-checks.yml@<40-character commit> # v1.4.0
with:
  ref: <the same commit>
```

The templates use two variables, `${ref}` (the commit, else `v<version>`) and `${ref_note}` (` # v<version>` when
there is a commit, else nothing), so they have no conditionals. The commit comes from the lock, never the network:

- **`rk upgrade`** (and `rk install`, `rk fleet upgrade`) asks repo-kit's remote where `v<version>` points, with
  `git ls-remote` (no new dependency, no token), follows an annotated tag to its commit, and records it in the lock
  as `ref_sha`, with the release it belongs to as `ref_sha_version`. Re-running is how an older repo gets its commit.
  If the tag now points somewhere other than the recorded commit, the upgrade refuses: tags never move. If the
  remote can't be reached, it warns, records none, and the callers pin the tag only, as before.
- **`rk sync`** renders from the lock alone, so it works offline. It uses the recorded commit only when
  `ref_sha_version` equals the pinned `version`; otherwise (a hand-edited pin, or a release PR that moved
  `version`) it pins the tag. It carries both fields through unchanged, so a lock never changes just because
  of a sync. A caller edited away from the recorded commit is hand-edited, and `managed-files` fails.
- **`rk doctor`** asks the remote once whether the tag still points at the recorded commit: FAIL if not, WARN if
  the remote can't be reached or the lock has no commit for the pinned release, nothing when the callers are
  `unmanaged`.

repo-kit's own callers are `unmanaged` (its workflows run as `uses: ./`), so none of this applies to it, and its
release PR (which moves `version` in `.repo-kit.toml` and the lock through release-please) needs no commit: the tag
doesn't exist until that PR merges.

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
- An upgrade that adds a template adds the file and updates the lock. Removed and renamed templates are applied by
  the same sync ([above](#removed-and-renamed-templates)).
