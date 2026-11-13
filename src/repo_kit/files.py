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


def template_variables(config: RepoConfig, owner: str, repo: str) -> dict[str, str]:
    return {
        "version": config.version,
        "profile": config.profile,
        "project_name": config.project.name or repo,
        "release_type": config.project.release_type,
        "default_base": config.default_base,
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


def load_layer(root: Path, layer: str) -> list[TemplateFile]:
    """The templates of one layer, from its manifest. A layer with no manifest has none."""
    folder = root / layer
    manifest = folder / MANIFEST_NAME
    if not manifest.is_file():
        return []
    where = f"templates/{layer}/{MANIFEST_NAME}"
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SyncError(f"{where}: {exc}") from exc
    if set(data) - {"files"}:
        raise SyncError(f"{where}: unknown key(s) {sorted(set(data) - {'files'})}")
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


@dataclass
class FilePlan:
    path: str
    mode: str
    action: Action
    detail: str = ""
    write: str | None = None  # the whole new file, when it must be written
    diff: str = ""


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
        writes = {Action.CREATE, Action.UPDATE, Action.REFUSE}
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


def _read_target(repo_root: Path, path: str) -> tuple[str | None, str]:
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
            template.path, "block", Action.REFUSE, "; ".join(refused), diff="".join(refuse_diffs)
        )
    elif touched:
        action = Action.CREATE if current is None else Action.UPDATE
        plan = FilePlan(
            template.path, "block", action, ", ".join(touched), text, "".join(update_diffs)
        )
    else:
        plan = FilePlan(template.path, "block", Action.ADOPT if adopted else Action.UNCHANGED)
    return plan, new_entry


def _plan_one(
    repo_root: Path,
    root: Path,
    template: TemplateFile,
    variables: dict[str, str],
    entry: dict[str, Any] | None,
) -> tuple[FilePlan, dict[str, Any]]:
    path, mode = template.path, template.mode
    # Render first, so a broken template is an error whatever state the repo is in.
    bodies = {
        block.name: _block_body(_render_source(root, block.source, variables))
        for block in template.blocks
    }
    wanted = _render_source(root, template.source, variables) if template.source else ""
    current, problem = _read_target(repo_root, path)

    if mode == "seeded" and problem in ("", _NOT_TEXT):
        new_entry: dict[str, Any] = {"layer": template.layer, "mode": mode}
        recorded = entry.get("sha256") if entry else None
        if current is None and not problem:
            new_entry["sha256"] = digest(wanted)
            return FilePlan(path, mode, Action.CREATE, write=wanted), new_entry
        # It exists, so it's the consumer's: never compared, never written.
        new_entry["sha256"] = recorded or (digest(current) if current is not None else "")
        return FilePlan(path, mode, Action.UNCHANGED), new_entry

    if problem:
        return FilePlan(path, mode, Action.REFUSE, problem), {}

    if mode == "block":
        return _plan_blocks(template, bodies, current, entry)

    new_entry = {"layer": template.layer, "mode": mode, "sha256": digest(wanted)}
    if current is None:
        return FilePlan(path, mode, Action.CREATE, write=wanted), new_entry
    recorded = entry.get("sha256") if entry else None
    action, detail, diff = _plan_compare(path, "file", current, wanted, recorded)
    write = wanted if action is Action.UPDATE else None
    return FilePlan(path, mode, action, detail, write, diff), new_entry


def plan_sync(
    repo_root: Path, config: RepoConfig, owner: str, repo: str, root: Path | None = None
) -> SyncPlan:
    root = root or templates_root()
    templates = collect_templates(root, config)
    variables = template_variables(config, owner, repo)
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
            repo_root, root, template, variables, old_files.get(template.path)
        )
        plan.files.append(file_plan)
        if file_plan.action is Action.REFUSE:
            if template.path in old_files:
                new_files[template.path] = old_files[template.path]
        else:
            new_files[template.path] = entry

    template_paths = {t.path for t in templates}
    for path in sorted(set(old_files) - template_paths):
        detail = "left in place; remove it by hand if it's no longer wanted"
        plan.files.append(FilePlan(path, old_files[path].get("mode", "?"), Action.ORPHAN, detail))
        new_files[path] = old_files[path]

    plan.lock = {"files": new_files, "version": config.version}
    plan.lock_changed = lock_text(plan.lock) != lock_text(old_lock)
    return plan


# --- applying ------------------------------------------------------------------------------


def apply_plan(repo_root: Path, plan: SyncPlan, changed: list[str]) -> None:
    """Write the plan. Refuses to write anything if any file was refused."""
    if plan.refused:
        names = ", ".join(f.path for f in plan.refused)
        raise SyncError(
            f"refused {names}: nothing was written. Move the change into repo-kit or "
            '.repo-kit.toml, or set `[overrides].files."<path>" = "unmanaged"`.'
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_root = git.common_dir(repo_root) / "repo-kit" / "backup" / stamp
    backed_up = False
    for file_plan in plan.files:
        if file_plan.write is None:
            continue
        target = repo_root / file_plan.path
        if target.is_file():
            backup = backup_root / file_plan.path
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            backed_up = True
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file_plan.write, encoding="utf-8", newline="\n")
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
        detail = f" ({f.detail})" if f.detail else ""
        lines.append(f"{f.action:>14}  {f.path}{detail}")
    shown = {Action.REFUSE, Action.UPDATE} if update_diffs else {Action.REFUSE}
    for f in plan.files:
        if f.action in shown and f.diff:
            lines += ["", f.diff.rstrip("\n")]
    return "\n".join(lines)


def run_sync(
    cwd: Path,
    *,
    check: bool,
    changed: list[str],
    say: Callable[[str], None] = print,
    root: Path | None = None,
) -> int:
    """The command. Returns the exit code: 1 when `--check` finds drift."""
    git.require_version(cwd)
    repo_root = git.toplevel(cwd)
    config = load_repo_config(repo_root)
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
    owner, repo = git.parse_owner_repo(git.remote_url(repo_root))
    plan = plan_sync(repo_root, config, owner, repo, root)
    say(summary(plan, update_diffs=check))
    if check:
        if plan.drifted:
            say("Out of sync with repo-kit: run `rk sync` and commit the result.")
            return 1
        return 0
    apply_plan(repo_root, plan, changed)
    return 0
