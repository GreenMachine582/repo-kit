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

from repo_kit import git
from repo_kit.config import RepoConfig, load_repo_config
from repo_kit.errors import RkError

LOCK_NAME = ".repo-kit.lock"
MANIFEST_NAME = "_manifest.toml"
MODES = ("managed", "block", "seeded")
_MANIFEST_KEYS = {"mode", "block", "source"}
# Files in a layer folder that are never templates.
_NOT_TEMPLATES = {MANIFEST_NAME, ".gitkeep"}


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
class TemplateFile:
    path: str  # target path in the consumer repo, POSIX style
    mode: str
    layer: str
    source: Path
    block: str | None = None


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
    result = []
    for raw_path, entry in data.get("files", {}).items():
        path = _safe_target(raw_path, where)
        unknown = set(entry) - _MANIFEST_KEYS
        if unknown:
            raise SyncError(f"{where}: `{path}` has unknown key(s) {sorted(unknown)}")
        mode = entry.get("mode")
        if mode not in MODES:
            raise SyncError(f"{where}: `{path}` mode must be one of {', '.join(MODES)}")
        block = entry.get("block")
        if (mode == "block") != (block is not None):
            raise SyncError(f"{where}: `{path}` needs `block` exactly when its mode is block")
        if block is not None and not re.fullmatch(r"[a-z][a-z0-9-]*", block):
            raise SyncError(f"{where}: `{path}` block name must be lowercase words and dashes")
        source = folder / _safe_target(entry.get("source", path), where)
        if not source.is_file():
            raise SyncError(f"{where}: `{path}` has no template file at {source}")
        result.append(TemplateFile(path, mode, layer, source, block))
    return result


def layers_for(config: RepoConfig) -> list[str]:
    layers = ["common", f"profiles/{config.profile}"]
    if config.extras.use_local_gth:
        layers.append("extras/use-local-gth")
    return layers


def collect_templates(root: Path, config: RepoConfig) -> list[TemplateFile]:
    found: dict[str, TemplateFile] = {}
    for layer in layers_for(config):
        for template in load_layer(root, layer):
            if template.path in found:
                other = found[template.path].layer
                raise SyncError(f"`{template.path}` is claimed by both {other} and {layer}")
            found[template.path] = template
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
    if not isinstance(data.get("files"), dict):
        raise SyncError(f"{LOCK_NAME}: expected a `files` table")
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


def _plan_one(
    repo_root: Path, template: TemplateFile, variables: dict[str, str], entry: dict[str, Any] | None
) -> tuple[FilePlan, dict[str, Any]]:
    source = f"templates/{template.layer}/{template.source.name}"
    wanted = lf(render(template.source.read_text(encoding="utf-8"), variables, source))
    target = repo_root / template.path
    current = lf(target.read_text(encoding="utf-8")) if target.is_file() else None
    recorded = entry.get("sha256") if entry else None
    new_entry: dict[str, Any] = {"layer": template.layer, "mode": template.mode}

    if template.mode == "seeded":
        if current is None:
            new_entry["sha256"] = digest(wanted)
            return FilePlan(template.path, "seeded", Action.CREATE, write=wanted), new_entry
        new_entry["sha256"] = recorded or digest(current)
        return FilePlan(template.path, "seeded", Action.UNCHANGED), new_entry

    new_entry["sha256"] = digest(wanted)
    if template.mode == "managed":
        if current is None:
            return FilePlan(template.path, "managed", Action.CREATE, write=wanted), new_entry
        action, detail, diff = _plan_compare(template.path, "file", current, wanted, recorded)
        write = wanted if action is Action.UPDATE else None
        return FilePlan(template.path, "managed", action, detail, write, diff), new_entry

    if template.block is None:  # load_layer guarantees it; kept explicit for the type checker
        raise SyncError(f"{template.path}: a block template needs a block name")
    new_entry["block"] = template.block
    text = current or ""
    span = find_block(text, template.path, template.block)
    if span is None:
        write = with_block(text, template.path, template.block, wanted, None)
        detail = f"{template.block} block added"
        action = Action.CREATE if current is None else Action.UPDATE
        return FilePlan(template.path, "block", action, detail, write), new_entry
    inner = block_inner(text, span)
    action, detail, diff = _plan_compare(template.path, "block", inner, wanted, recorded)
    write = None
    if action is Action.UPDATE:
        write = with_block(text, template.path, template.block, wanted, span)
    return FilePlan(template.path, "block", action, detail, write, diff), new_entry


def plan_sync(
    repo_root: Path, config: RepoConfig, owner: str, repo: str, root: Path | None = None
) -> SyncPlan:
    templates = collect_templates(root or templates_root(), config)
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
        file_plan, entry = _plan_one(repo_root, template, variables, old_files.get(template.path))
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


def summary(plan: SyncPlan) -> str:
    lines = []
    for f in plan.files:
        detail = f" ({f.detail})" if f.detail else ""
        lines.append(f"{f.action:>14}  {f.path}{detail}")
    for f in plan.refused:
        if f.diff:
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
    repo_root = git.toplevel(cwd)
    config = load_repo_config(repo_root)
    if not config.version:
        raise SyncError(
            "`version` is empty in .repo-kit.toml: set the repo-kit release to pin, such as "
            '`version = "1.0.0"`'
        )
    owner, repo = git.parse_owner_repo(git.remote_url(repo_root))
    plan = plan_sync(repo_root, config, owner, repo, root)
    say(summary(plan))
    if check:
        if plan.drifted:
            say("Out of sync with repo-kit: run `rk sync` and commit the result.")
            return 1
        return 0
    apply_plan(repo_root, plan, changed)
    return 0
