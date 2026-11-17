"""`rk sync`: render the templates and write them by ownership mode (managed, block, seeded).

Each template layer has a `_manifest.toml` naming its files and their modes. The whole sync is
planned first and nothing is written if any file is refused. `.repo-kit.lock` records what was
rendered, so a hand edit can be told apart from an older render. See docs/shared-files.md.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import string
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

import repo_kit
from repo_kit import git
from repo_kit.config import RepoConfig, load_repo_config
from repo_kit.errors import RkError

LOCK_NAME = ".repo-kit.lock"
MANIFEST_NAME = "_manifest.toml"
MODES = ("managed", "block", "seeded")
_MANIFEST_KEYS = {"mode", "blocks", "source"}
_MANIFEST_TABLES = {"files", "renames"}
# The version of an engine that isn't a release: a source tree, or CI running rk from a checkout.
UNRELEASED = "0.0.0"


class SyncError(RkError):
    """A template, manifest or lock problem, or a sync that refused to write."""


# --- placeholders --------------------------------------------------------------------------

_PLACEHOLDER = r"""
\$(?:
    (?P<escaped>(?!))                   |
    \{(?P<braced>[a-z][a-z0-9_]*)\}     |
    (?P<named>(?!))                     |
    (?P<invalid>(?!))
)
"""


class _Placeholders(string.Template):
    """Only `${lowercase_name}` is a placeholder. `${{ github.x }}`, `$VAR`, `${UPPER}` and `$$`
    are left exactly as written, so workflow files and shell scripts read naturally."""

    flags = re.RegexFlag(0)  # case-sensitive: `${UPPER}` is never a placeholder
    # Template compiles a string `pattern` when the subclass is created; typeshed only knows the
    # compiled form. The other groups never match, so `$` alone stays literal.
    pattern = _PLACEHOLDER  # type: ignore[assignment]


def render(text: str, variables: dict[str, str], source: str) -> str:
    try:
        return _Placeholders(text).substitute(variables)
    except KeyError as exc:
        known = ", ".join(sorted(variables))
        raise SyncError(
            f"{source}: unknown placeholder ${{{exc.args[0]}}} (known: {known})"
        ) from exc


# The PR-title paragraph of the CONTRIBUTING blocks, by `[checks].pr_title`. Built here because
# templates can't branch.
PR_TITLE_TEXT = {
    "not-conventional": (
        "PR titles are **not** Conventional Commits: write a plain sentence, such as "
        "`Add the folder picker`. The merge commit carries the title, and release-please reads a "
        "conventional line there as one more change, so a `feat: …` title would list that change "
        "twice. GitHub suggests a one-commit PR's subject as its title, so rewrite it. Release PRs "
        "(`release: …`, `chore(main): release …`) are allowed."
    ),
    "conventional": (
        "PR titles are Conventional Commits too, such as `feat: add the folder picker`, checked "
        "the same way as the commits."
    ),
}


def extra_files_json(config: RepoConfig) -> str:
    """The `extra-files` key of the release-please package, as JSON text that continues the line
    before it (so a comma first), or nothing. Built here because templates can't loop."""
    if not config.project.extra_files:
        return ""
    entries = [
        {"type": e.type, "path": e.path} | ({"jsonpath": e.jsonpath} if e.jsonpath else {})
        for e in config.project.extra_files
    ]
    body = json.dumps(entries, indent=2).replace("\n", "\n      ")
    return f',\n      "extra-files": {body}'


def template_variables(config: RepoConfig, owner: str, repo: str) -> dict[str, str]:
    return {
        "commit_header_max": str(config.checks.commit_header_max),
        "pr_title_rule": PR_TITLE_TEXT[config.checks.pr_title],
        "version": config.version,
        "profile": config.profile,
        "project_name": config.project.name or repo,
        "release_type": config.project.release_type,
        "extra_files": extra_files_json(config),
        "default_base": config.default_base,
        "release_branch": config.release_branch,
        # Where `release.yml` merges a release back to; empty turns the back-merge off.
        "back_merge_into": config.default_base if config.release.back_merge else "",
        # Quoted as JSON, which YAML reads as a double-quoted string, so any label name is safe.
        "conflict_label": json.dumps(config.update_prs.conflict_label),
        "owner": owner,
        "repo": repo,
    }


# --- templates and manifests ---------------------------------------------------------------


@dataclass(frozen=True)
class BlockTemplate:
    name: str
    layer: str
    source: Path


@dataclass(frozen=True)
class TemplateFile:
    path: str  # target path in the consumer repo, POSIX style
    mode: str
    layer: str
    source: Path | None = None  # the template file of a managed or seeded target
    blocks: tuple[BlockTemplate, ...] = ()  # the blocks of a block target, in the order appended

    @property
    def sources(self) -> tuple[Path, ...]:
        """Every template file this target is rendered from."""
        if self.source is not None:
            return (self.source,)
        return tuple(block.source for block in self.blocks)


def templates_root() -> Path:
    """`repo_kit/templates` in an installed wheel, else `templates/` in a repo-kit checkout."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "templates", here.parents[1] / "templates"):
        if candidate.is_dir():
            return candidate
    raise SyncError("can't find repo-kit's templates: reinstall repo-kit")


def _safe_target(path: str, where: str) -> str:
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts or "\\" in path:
        raise SyncError(f"{where}: `{path}` must be a relative path inside the repo")
    return pure.as_posix()


def _read_manifest(root: Path, layer: str) -> tuple[dict[str, Any], str]:
    """A layer's manifest as a table (empty if it has none), and its name for error messages."""
    manifest = root / layer / MANIFEST_NAME
    where = f"templates/{layer}/{MANIFEST_NAME}"
    if not manifest.is_file():
        return {}, where
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SyncError(f"{where}: {exc}") from exc
    if set(data) - _MANIFEST_TABLES:
        raise SyncError(f"{where}: unknown key(s) {sorted(set(data) - _MANIFEST_TABLES)}")
    return data, where


def load_renames(root: Path, config: RepoConfig, template_paths: set[str]) -> dict[str, str]:
    """The `[renames]` of every layer: old target path -> the path its template moved to."""
    found: dict[str, str] = {}
    for layer in layers_for(config):
        data, where = _read_manifest(root, layer)
        table = data.get("renames", {})
        if not isinstance(table, dict):
            raise SyncError(f"{where}: `renames` must be a table of old path = new path")
        for raw_old, raw_new in table.items():
            if not isinstance(raw_new, str):
                raise SyncError(f"{where}: the rename of `{raw_old}` must name a path")
            old, new = _safe_target(raw_old, where), _safe_target(raw_new, where)
            if old in template_paths:
                raise SyncError(f"{where}: `{old}` is renamed but is still a template")
            if new not in template_paths:
                raise SyncError(f"{where}: `{old}` is renamed to `{new}`, which isn't a template")
            if found.setdefault(old, new) != new:
                raise SyncError(f"{where}: `{old}` is renamed to two different paths")
    return found


def load_layer(root: Path, layer: str) -> list[TemplateFile]:
    """The templates of one layer, from its manifest. A layer with no manifest has none."""
    folder = root / layer
    data, where = _read_manifest(root, layer)
    files = data.get("files", {})
    if not isinstance(files, dict):
        raise SyncError(f"{where}: `files` must be a table")
    result = []
    for raw_path, entry in files.items():
        path = _safe_target(raw_path, where)
        if not isinstance(entry, dict):
            raise SyncError(f"{where}: `{path}` must be a table")
        unknown = set(entry) - _MANIFEST_KEYS
        if unknown:
            raise SyncError(f"{where}: `{path}` has unknown key(s) {sorted(unknown)}")
        mode = entry.get("mode")
        if mode not in MODES:
            raise SyncError(f"{where}: `{path}` mode must be one of {', '.join(MODES)}")
        if (mode == "block") != ("blocks" in entry):
            raise SyncError(f"{where}: `{path}` needs `blocks` exactly when its mode is block")
        if mode == "block":
            if "source" in entry:
                raise SyncError(f"{where}: `{path}` names its templates in `blocks`, not `source`")
            blocks = _load_blocks(folder, layer, path, entry["blocks"], where)
            result.append(TemplateFile(path, mode, layer, blocks=blocks))
            continue
        source = _template_source(folder, entry.get("source", path), path, where)
        result.append(TemplateFile(path, mode, layer, source))
    return result


def _template_source(folder: Path, name: Any, path: str, where: str) -> Path:
    if not isinstance(name, str):
        raise SyncError(f"{where}: `{path}` template file must be a string")
    source = folder / _safe_target(name, where)
    if not source.is_file():
        raise SyncError(f"{where}: `{path}` has no template file at {source}")
    return source


def _load_blocks(
    folder: Path, layer: str, path: str, blocks: Any, where: str
) -> tuple[BlockTemplate, ...]:
    """A block entry's `blocks` table: block name -> template file, in the order appended."""
    if not isinstance(blocks, dict) or not blocks:
        raise SyncError(f"{where}: `{path}` `blocks` must be a table of name = template file")
    result = []
    for name, source in blocks.items():
        if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            raise SyncError(f"{where}: `{path}` block name must be lowercase words and dashes")
        result.append(BlockTemplate(name, layer, _template_source(folder, source, path, where)))
    return tuple(result)


def layers_for(config: RepoConfig) -> list[str]:
    layers = ["common", f"profiles/{config.profile}"]
    if config.extras.use_local_gth:
        layers.append("extras/use-local-gth")
    return layers


def collect_templates(root: Path, config: RepoConfig) -> list[TemplateFile]:
    found: dict[str, TemplateFile] = {}
    for layer in layers_for(config):
        for template in load_layer(root, layer):
            first = found.get(template.path)
            if first is None:
                found[template.path] = template
                continue
            # Layers may share a file only by each adding their own blocks to it.
            if first.mode != "block" or template.mode != "block":
                raise SyncError(f"`{template.path}` is claimed by both {first.layer} and {layer}")
            names = {block.name for block in first.blocks}
            for block in template.blocks:
                if block.name in names:
                    owner = next(b.layer for b in first.blocks if b.name == block.name)
                    raise SyncError(
                        f"`{template.path}` block `{block.name}` is claimed by both "
                        f"{owner} and {layer}"
                    )
            found[template.path] = TemplateFile(
                first.path, "block", first.layer, blocks=first.blocks + template.blocks
            )
    return list(found.values())


# --- content -------------------------------------------------------------------------------


def lf(text: str) -> str:
    """Compare and hash with LF endings, so CRLF from `core.autocrlf` is never an edit."""
    return text.replace("\r\n", "\n")


def digest(text: str) -> str:
    return hashlib.sha256(lf(text).encode("utf-8")).hexdigest()


def markers(path: str, name: str) -> tuple[str, str]:
    if path.endswith(".md"):
        return f"<!-- >>> repo-kit: {name} -->", f"<!-- <<< repo-kit: {name} -->"
    return f"# >>> repo-kit: {name}", f"# <<< repo-kit: {name}"


def find_block(text: str, path: str, name: str) -> tuple[int, int] | None:
    """Line indexes of the start and end markers of block `name`, or None if it isn't there."""
    start, end = markers(path, name)
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines) if line.strip() == start]
    ends = [i for i, line in enumerate(lines) if line.strip() == end]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise SyncError(f"{path}: the `{name}` block markers are missing, repeated or out of order")
    return starts[0], ends[0]


def block_inner(text: str, span: tuple[int, int]) -> str:
    inner = text.split("\n")[span[0] + 1 : span[1]]
    return "\n".join(inner) + "\n" if inner else ""


def with_block(text: str, path: str, name: str, body: str, span: tuple[int, int] | None) -> str:
    """`text` with block `name` holding `body`, appended at the end if it isn't there yet."""
    start, end = markers(path, name)
    body_lines = body.rstrip("\n").split("\n") if body else []
    if span is None:
        prefix = text if not text or text.endswith("\n") else text + "\n"
        separator = "\n" if prefix else ""
        return prefix + separator + "\n".join([start, *body_lines, end]) + "\n"
    lines = text.split("\n")
    return "\n".join([*lines[: span[0] + 1], *body_lines, *lines[span[1] :]])


def _diff(path: str, current: str, wanted: str) -> str:
    return "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            wanted.splitlines(keepends=True),
            fromfile=f"{path} (now)",
            tofile=f"{path} (repo-kit)",
        )
    )


# --- the lock ------------------------------------------------------------------------------


def read_lock(repo_root: Path) -> dict[str, Any]:
    path = repo_root / LOCK_NAME
    if not path.is_file():
        return {"version": "", "files": {}}
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SyncError(f"{LOCK_NAME}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise SyncError(f"{LOCK_NAME}: expected a `files` table")
    for path, entry in data["files"].items():
        if not isinstance(entry, dict):
            raise SyncError(f"{LOCK_NAME}: the entry for `{path}` must be a table")
    return data


def lock_text(lock: dict[str, Any]) -> str:
    return json.dumps(lock, indent=2, sort_keys=True) + "\n"


# --- planning ------------------------------------------------------------------------------


class Action(StrEnum):
    CREATE = "created"
    UPDATE = "updated"
    ADOPT = "taken over"
    UNCHANGED = "unchanged"
    REFUSE = "refused"
    SKIP = "skipped"
    ORPHAN = "not a template"
    REMOVE = "removed"


@dataclass
class FilePlan:
    path: str
    mode: str
    action: Action
    detail: str = ""
    write: str | None = None  # the whole new file, when it must be written
    diff: str = ""
    was: str = ""  # the mode the lock recorded, when the template has changed it
    delete: bool = False  # the file itself is to be deleted
    adoptable: bool = False  # a refusal that `--adopt` may offer to settle by taking the file over
    note: str = ""  # for the change list, when the lock entry is all that changes


@dataclass
class SyncPlan:
    files: list[FilePlan] = field(default_factory=list)
    lock: dict[str, Any] = field(default_factory=dict)
    lock_changed: bool = False

    @property
    def refused(self) -> list[FilePlan]:
        return [f for f in self.files if f.action is Action.REFUSE]

    @property
    def drifted(self) -> bool:
        """What `--check` fails on: anything sync would write or refuse, or a stale lock."""
        writes = {Action.CREATE, Action.UPDATE, Action.REFUSE, Action.REMOVE}
        return self.lock_changed or any(f.action in writes for f in self.files)


def _plan_compare(
    path: str, mode: str, current: str, wanted: str, recorded: str | None
) -> tuple[Action, str, str]:
    """For managed files and block contents: the action, a detail and a diff."""
    if current == wanted:
        return (Action.UNCHANGED if recorded else Action.ADOPT), "", ""
    diff = _diff(path, current, wanted)
    if recorded is None:
        return Action.REFUSE, "exists, differs, and isn't in the lock", diff
    if digest(current) != recorded:
        return Action.REFUSE, f"{mode} content was edited by hand", diff
    return Action.UPDATE, "", diff


_NOT_TEXT = "isn't UTF-8 text"


def read_target(repo_root: Path, path: str) -> tuple[str | None, str]:
    """The target's text with LF endings (None if it isn't there), and why rk can't own it."""
    target = repo_root / path
    # A symlink, in the path or at its end, must not take a write outside the repo.
    if not target.resolve().is_relative_to(repo_root.resolve()):
        return None, "resolves to a path outside the repo"
    if target.is_dir():
        return None, "is a directory"
    if not target.is_file():
        return None, ""
    try:
        return lf(target.read_text(encoding="utf-8")), ""
    except UnicodeDecodeError:
        return None, _NOT_TEXT


def line_ending(target: Path) -> str:
    """The line ending an existing file uses (CRLF if most of its lines do), else LF."""
    try:
        data = target.read_bytes()
    except OSError:
        return "\n"
    crlf = data.count(b"\r\n")
    return "\r\n" if crlf and crlf * 2 >= data.count(b"\n") else "\n"


def _render_source(root: Path, source: Path, variables: dict[str, str]) -> str:
    label = f"templates/{source.relative_to(root).as_posix()}"
    return lf(render(source.read_text(encoding="utf-8"), variables, label))


def _block_body(text: str) -> str:
    """A block's contents end in exactly one newline, which is how they read back from the file."""
    stripped = text.rstrip("\n")
    return stripped + "\n" if stripped else ""


def _plan_blocks(
    template: TemplateFile,
    bodies: dict[str, str],
    current: str | None,
    entry: dict[str, Any] | None,
    force: bool = False,
) -> tuple[FilePlan, dict[str, Any]]:
    """One plan for a file's blocks: refused if any block is, else every block in one write."""
    old_blocks = entry.get("blocks") if entry else None
    recorded: dict[str, Any] = old_blocks if isinstance(old_blocks, dict) else {}
    # A recorded block whose template has gone is left alone, in the file and in the lock.
    new_blocks: dict[str, Any] = dict(recorded)
    text = current or ""
    touched: list[str] = []
    refused: list[str] = []
    update_diffs: list[str] = []
    refuse_diffs: list[str] = []
    adopted = False

    for block in template.blocks:
        wanted = bodies[block.name]
        new_blocks[block.name] = {"layer": block.layer, "sha256": digest(wanted)}
        span = find_block(text, template.path, block.name)
        if span is None:
            text = with_block(text, template.path, block.name, wanted, None)
            touched.append(f"{block.name} block added")
            continue
        old = recorded.get(block.name)
        sha = old.get("sha256") if isinstance(old, dict) else None
        if force:
            sha = digest(block_inner(text, span))  # adopting: what is there counts as unedited
        action, detail, diff = _plan_compare(
            template.path, "block", block_inner(text, span), wanted, sha
        )
        if action is Action.REFUSE:
            refused.append(f"{block.name} block: {detail}")
            refuse_diffs.append(diff)
        elif action is Action.UPDATE:
            text = with_block(text, template.path, block.name, wanted, span)
            touched.append(f"{block.name} block updated")
            update_diffs.append(diff)
        elif action is Action.ADOPT:
            adopted = True

    new_entry = {"mode": "block", "blocks": new_blocks}
    if refused:
        plan = FilePlan(
            template.path,
            "block",
            Action.REFUSE,
            "; ".join(refused),
            diff="".join(refuse_diffs),
            adoptable=True,
        )
    elif touched:
        action = Action.CREATE if current is None else Action.UPDATE
        plan = FilePlan(
            template.path, "block", action, ", ".join(touched), text, "".join(update_diffs)
        )
    else:
        plan = FilePlan(template.path, "block", Action.ADOPT if adopted else Action.UNCHANGED)
    return plan, new_entry


def _mode_change(entry: dict[str, Any] | None, mode: str) -> str:
    """The mode the lock recorded if it differs from the template's now, else "". An entry with no
    mode (an old lock) is taken to match."""
    old = entry.get("mode") if entry else None
    return old if isinstance(old, str) and old != mode else ""


def _plan_one(
    repo_root: Path,
    root: Path,
    template: TemplateFile,
    variables: dict[str, str],
    entry: dict[str, Any] | None,
    force: bool = False,
) -> tuple[FilePlan, dict[str, Any]]:
    path, mode = template.path, template.mode
    # Render first, so a broken template is an error whatever state the repo is in.
    bodies = {
        block.name: _block_body(_render_source(root, block.source, variables))
        for block in template.blocks
    }
    wanted = _render_source(root, template.source, variables) if template.source else ""
    current, problem = read_target(repo_root, path)

    if mode == "seeded" and problem in ("", _NOT_TEXT):
        new_entry: dict[str, Any] = {"layer": template.layer, "mode": mode}
        was = _mode_change(entry, mode)
        recorded = entry.get("sha256") if entry else None
        if current is None and not problem:
            if entry is not None:
                # The lock has it, so it was created once and the consumer deleted it: theirs now.
                return FilePlan(path, mode, Action.UNCHANGED, "deleted by the repo; left"), entry
            new_entry["sha256"] = digest(wanted)
            return FilePlan(path, mode, Action.CREATE, write=wanted), new_entry
        # It exists, so it's the consumer's: never compared, never written.
        new_entry["sha256"] = recorded or (digest(current) if current is not None else "")
        return FilePlan(path, mode, Action.UNCHANGED, was=was), new_entry

    if problem:
        return FilePlan(path, mode, Action.REFUSE, problem), {}

    if mode == "block":
        plan, block_entry = _plan_blocks(template, bodies, current, entry, force)
        plan.was = _mode_change(entry, mode)
        return plan, block_entry

    new_entry = {"layer": template.layer, "mode": mode, "sha256": digest(wanted)}
    if current is None:
        return FilePlan(path, mode, Action.CREATE, write=wanted), new_entry
    was = _mode_change(entry, mode)
    # A hash recorded under another mode (a seeded file's, say) says nothing about edits here.
    recorded = entry.get("sha256") if entry and not was else None
    if force:
        recorded = digest(current)  # adopting: what is there counts as unedited
    action, detail, diff = _plan_compare(path, "file", current, wanted, recorded)
    write = wanted if action is Action.UPDATE else None
    adoptable = action is Action.REFUSE
    return FilePlan(path, mode, action, detail, write, diff, was, adoptable=adoptable), new_entry


def _strip_block(text: str, path: str, name: str) -> str:
    """`text` without block `name`, its markers and the blank line that set it apart."""
    span = find_block(text, path, name)
    if span is None:
        return text
    lines = text.split("\n")
    start = span[0]
    if start > 0 and not lines[start - 1].strip():
        start -= 1
    return "\n".join(lines[:start] + lines[span[1] + 1 :])


def _plan_removal(
    repo_root: Path, path: str, entry: dict[str, Any], new_path: str | None
) -> tuple[FilePlan, bool]:
    """What to do with a locked file whose template has gone: the plan, and whether the lock
    keeps its entry (only a refusal does). A rename is a removal here: the new path is planned
    like any other template."""
    mode = entry["mode"]
    why = f"renamed to {new_path}" if new_path else "no longer a template"
    dropped = f"dropped from the lock ({why})"
    if mode == "seeded":
        # Seeded files are the repo's once created: never touched, only forgotten.
        plan = FilePlan(path, mode, Action.UNCHANGED, f"{why}; yours, left", note=dropped)
        return plan, False
    current, problem = read_target(repo_root, path)
    if problem:
        return FilePlan(path, mode, Action.REFUSE, f"{why}, but it {problem}"), True
    if current is None:
        return FilePlan(path, mode, Action.UNCHANGED, f"{why}; already gone", note=dropped), False
    edited = (
        f"{why}, but {path} has {mode} content edited by hand: delete it or restore it, then re-run"
    )
    if mode == "managed":
        if digest(current) != entry.get("sha256"):
            return FilePlan(path, mode, Action.REFUSE, edited), True
        return FilePlan(path, mode, Action.REMOVE, why, delete=True), False
    blocks = entry.get("blocks")
    text, stripped = current, []
    for name, block in sorted((blocks if isinstance(blocks, dict) else {}).items()):
        span = find_block(text, path, name)
        if span is None:
            continue
        sha = block.get("sha256") if isinstance(block, dict) else None
        if digest(block_inner(text, span)) != sha:
            return FilePlan(path, mode, Action.REFUSE, edited), True
        text = _strip_block(text, path, name)
        stripped.append(name)
    if not stripped:
        return FilePlan(path, mode, Action.UNCHANGED, f"{why}; no blocks left", note=dropped), False
    detail = f"{why}; {', '.join(stripped)} block removed"
    return FilePlan(path, mode, Action.REMOVE, detail, write=text), False


_PROJECT_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$")


def project_version(repo_root: Path) -> str:
    """The version the release manifest is seeded with: `pyproject.toml`'s `[project].version`,
    else `package.json`'s `version`, else 0.0.0. A dynamic or odd value counts as none."""
    found: Any = None
    try:
        pyproject = repo_root / "pyproject.toml"
        package = repo_root / "package.json"
        if pyproject.is_file():
            found = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
            found = found.get("version") if isinstance(found, dict) else None
        elif package.is_file():
            found = json.loads(package.read_text(encoding="utf-8"))
            found = found.get("version") if isinstance(found, dict) else None
    except (OSError, UnicodeDecodeError, ValueError):
        found = None
    if isinstance(found, str) and _PROJECT_VERSION.match(found):
        return found
    return UNRELEASED


def plan_sync(
    repo_root: Path,
    config: RepoConfig,
    owner: str,
    repo: str,
    root: Path | None = None,
    adopt: frozenset[str] = frozenset(),
) -> SyncPlan:
    """The plan for every template. Paths in `adopt` are taken over as they stand, whatever the
    lock says about them: only `--adopt`, after a yes for that file, passes any."""
    root = root or templates_root()
    templates = collect_templates(root, config)
    variables = template_variables(config, owner, repo)
    # Read from the repo, so not in template_variables: only the seeded release manifest uses it.
    variables["current_version"] = project_version(repo_root)
    old_lock = read_lock(repo_root)
    old_files: dict[str, Any] = old_lock["files"]
    unmanaged = set(config.overrides.files)
    plan = SyncPlan()
    new_files: dict[str, Any] = {}

    for template in sorted(templates, key=lambda t: t.path):
        if template.path in unmanaged:
            plan.files.append(FilePlan(template.path, template.mode, Action.SKIP, "unmanaged"))
            if template.path in old_files:
                new_files[template.path] = old_files[template.path]
            continue
        file_plan, entry = _plan_one(
            repo_root,
            root,
            template,
            variables,
            old_files.get(template.path),
            template.path in adopt,
        )
        plan.files.append(file_plan)
        if file_plan.action is Action.REFUSE:
            if template.path in old_files:
                new_files[template.path] = old_files[template.path]
        else:
            new_files[template.path] = entry

    template_paths = {t.path for t in templates}
    renames = load_renames(root, config, template_paths)
    for path in sorted(set(old_files) - template_paths):
        entry = old_files[path]
        if path in unmanaged or entry.get("mode") not in MODES:
            detail = "left in place; remove it by hand if it's no longer wanted"
            plan.files.append(FilePlan(path, entry.get("mode", "?"), Action.ORPHAN, detail))
            new_files[path] = entry
            continue
        file_plan, keep = _plan_removal(repo_root, path, entry, renames.get(path))
        plan.files.append(file_plan)
        if keep:
            new_files[path] = entry

    plan.lock = {"files": new_files, "version": config.version}
    plan.lock_changed = lock_text(plan.lock) != lock_text(old_lock)
    return plan


# --- applying ------------------------------------------------------------------------------


def raise_if_refused(plan: SyncPlan) -> None:
    if plan.refused:
        names = ", ".join(f.path for f in plan.refused)
        raise SyncError(
            f"refused {names}: nothing was written. Move the change into repo-kit or "
            '.repo-kit.toml, or set `[overrides].files."<path>" = "unmanaged"`.'
        )


def new_backup_root(repo_root: Path) -> Path:
    """Where this run copies what it replaces: in the Git dir, so never in the working tree."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return git.common_dir(repo_root) / "repo-kit" / "backup" / stamp


def apply_plan(
    repo_root: Path, plan: SyncPlan, changed: list[str], backup_root: Path | None = None
) -> None:
    """Write the plan. Refuses to write anything if any file was refused."""
    raise_if_refused(plan)
    backup_root = backup_root or new_backup_root(repo_root)
    backed_up = False
    for file_plan in plan.files:
        if file_plan.was and file_plan.write is None:
            changed.append(f"recorded {file_plan.path} as {file_plan.mode} (was {file_plan.was})")
        if file_plan.note and file_plan.write is None and not file_plan.delete:
            changed.append(f"{file_plan.path}: {file_plan.note}")
        if file_plan.write is None and not file_plan.delete:
            continue
        target = repo_root / file_plan.path
        if target.is_file():
            backup = backup_root / file_plan.path
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            backed_up = True
        if file_plan.delete:
            target.unlink()
            changed.append(f"{file_plan.action} {file_plan.path} ({file_plan.detail})")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        text = file_plan.write or ""
        if line_ending(target) == "\r\n":
            text = text.replace("\n", "\r\n")
        # newline="": the text already has the endings the file is to keep.
        with target.open("w", encoding="utf-8", newline="") as f:
            f.write(text)
        changed.append(f"{file_plan.action} {file_plan.path}")
    if plan.lock_changed:
        (repo_root / LOCK_NAME).write_text(lock_text(plan.lock), encoding="utf-8", newline="\n")
        changed.append(f"wrote {LOCK_NAME}")
    if backed_up:
        changed.append(f"backed up the replaced files to {backup_root}")


def summary(plan: SyncPlan, *, update_diffs: bool = False) -> str:
    """One line per file, then the diff of each refusal (and of each update, for `--check`)."""
    lines = []
    for f in plan.files:
        notes = [f.detail] if f.detail else []
        if f.was:
            notes.append(f"mode changed from {f.was}")
        detail = f" ({'; '.join(notes)})" if notes else ""
        lines.append(f"{f.action:>14}  {f.path}{detail}")
    shown = {Action.REFUSE, Action.UPDATE} if update_diffs else {Action.REFUSE}
    for f in plan.files:
        if f.action in shown and f.diff:
            lines += ["", f.diff.rstrip("\n")]
    return "\n".join(lines)


def _adopt(
    plan: SyncPlan,
    replan: Callable[[frozenset[str]], SyncPlan],
    confirm: Callable[[str], bool],
    say: Callable[[str], None],
) -> tuple[SyncPlan, list[str]]:
    """Offer each refused file that adoption can settle: its diff, then a question. A yes takes
    the file over (the render replaces it, and the lock records the render); a no leaves it. Only
    refusals of that kind are offered: a directory, a binary file or a removed template's edited
    file stays refused, and so aborts the sync."""
    accepted: set[str] = set()
    declined: list[str] = []
    for offer in (f for f in plan.refused if f.adoptable):
        say(f"\n{offer.detail}\n{offer.diff.rstrip(chr(10))}")
        if confirm(f"Take over {offer.path} and replace it with repo-kit's render?"):
            accepted.add(offer.path)
        else:
            declined.append(offer.path)
    if accepted:
        plan = replan(frozenset(accepted))
    for f in plan.files:
        if f.path in accepted and f.action is Action.UPDATE:
            f.action, f.detail = Action.ADOPT, "replaced with the render"
        elif f.path in declined and f.action is Action.REFUSE:
            f.action, f.detail, f.diff = Action.SKIP, "declined; left as it is", ""
    return plan, declined


def require_pin(config: RepoConfig) -> None:
    """The pin must be set, and be the running engine's version if that is a release."""
    if not config.version:
        raise SyncError(
            "`version` is empty in .repo-kit.toml: set the repo-kit release to pin, such as "
            '`version = "1.0.0"`'
        )
    # Another release's templates would be written under this pin, and CI's `--check`, which runs
    # the pinned engine, would then fail. An unreleased engine can't be compared, so it's let be.
    engine = repo_kit.__version__
    if engine not in (UNRELEASED, config.version):
        raise SyncError(
            f".repo-kit.toml pins repo-kit {config.version}, but this rk is {engine}: move the "
            f"pin with `rk upgrade`, or install rk {config.version}"
        )


def run_sync(
    cwd: Path,
    *,
    check: bool,
    changed: list[str],
    say: Callable[[str], None] = print,
    root: Path | None = None,
    adopt: bool = False,
    confirm: Callable[[str], bool] | None = None,
) -> int:
    """The command. Returns the exit code: 1 when `--check` finds drift."""
    git.require_version(cwd)
    repo_root = git.toplevel(cwd)
    config = load_repo_config(repo_root)
    require_pin(config)
    owner, repo = git.parse_owner_repo(git.remote_url(repo_root))
    plan = plan_sync(repo_root, config, owner, repo, root)
    declined: list[str] = []
    if adopt and not check:
        plan, declined = _adopt(
            plan,
            lambda accepted: plan_sync(repo_root, config, owner, repo, root, accepted),
            confirm or (lambda _question: False),
            say,
        )
    say(summary(plan, update_diffs=check))
    if check:
        if plan.drifted:
            say("Out of sync with repo-kit: run `rk sync` and commit the result.")
            return 1
        return 0
    apply_plan(repo_root, plan, changed)
    changed.extend(f"declined {path} (left as it is)" for path in declined)
    return 0
