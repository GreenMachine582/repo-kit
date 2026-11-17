"""`.repo-kit.toml` and the per-machine user config.

Parse with `tomllib`, reject unknown keys, apply defaults. See docs/configuration.md.

Each table is a frozen dataclass. One generic loader walks the parsed TOML against the dataclass
fields, so adding a key means adding a field: there is no second list of allowed keys to forget.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
import typing
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from repo_kit.errors import RkError

REPO_CONFIG_NAME = ".repo-kit.toml"
USER_CONFIG_ENV = "REPO_KIT_CONFIG"

# The branch `wt start` comes off when `--from` isn't given, per profile.
PROFILE_DEFAULT_BASE = {"dev-main": "dev"}

# The branch releases are cut from, per profile: it only takes release PRs from the default base.
PROFILE_RELEASE_BRANCH = {"dev-main": "main"}

# Branches that only change through PRs, per profile: never a PR head that the bot updates.
PROFILE_LONG_LIVED = {"dev-main": ("dev", "main")}

# `[checks].pr_title`: whether a PR title must not, or must, be a conventional commit.
PR_TITLE_RULES = ("not-conventional", "conventional")

# `[project].extra_files` types. Structured ones need a `jsonpath` to the version; `generic` reads
# `x-release-please-version` markers in the file instead.
EXTRA_FILE_TYPES = ("generic", "json", "toml", "yaml")

# Folder names of sync clients that break venvs and Git internals.
_SYNCED_PREFIXES = ("onedrive", "dropbox", "icloud")


class ConfigError(RkError):
    """A config file that can't be read, or has unknown keys or wrong types."""


@dataclass(frozen=True)
class ExtraFile:
    """One file release-please bumps next to the release type's own: its `extra-files` entry."""

    path: str
    type: str = "generic"
    # Where the version is, for the structured types (json, toml, yaml); empty for generic.
    jsonpath: str = ""


@dataclass(frozen=True)
class ProjectConfig:
    name: str = ""
    release_type: str = "python"
    extra_files: tuple[ExtraFile, ...] = ()


@dataclass(frozen=True)
class ChecksConfig:
    required: tuple[str, ...] = ()
    pr_title: str = "not-conventional"
    commit_header_max: int = 100


@dataclass(frozen=True)
class ReleaseConfig:
    app: str = "gth-release-bot"
    # The App's numeric ID, for ruleset bypass lists: a private App can't be looked up by name.
    app_id: int = 5058502
    back_merge: bool = True


@dataclass(frozen=True)
class UpdatePrsConfig:
    enabled: bool = True
    skip_labels: tuple[str, ...] = ("no-auto-update", "wip")
    conflict_label: str = "needs-merge"


@dataclass(frozen=True)
class WorktreeConfig:
    setup: tuple[str, ...] = ()
    check: str = ""
    env_dirs: tuple[str, ...] = (".venv",)


@dataclass(frozen=True)
class ExtrasConfig:
    use_local_gth: bool = False
    gth_packages: tuple[str, ...] = ()


@dataclass(frozen=True)
class OverridesConfig:
    # Path -> ownership mode. "unmanaged" is the only mode an override can set.
    files: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RepoConfig:
    version: str = ""
    profile: str = "dev-main"
    project: ProjectConfig = field(default_factory=ProjectConfig)
    checks: ChecksConfig = field(default_factory=ChecksConfig)
    release: ReleaseConfig = field(default_factory=ReleaseConfig)
    update_prs: UpdatePrsConfig = field(default_factory=UpdatePrsConfig)
    worktree: WorktreeConfig = field(default_factory=WorktreeConfig)
    extras: ExtrasConfig = field(default_factory=ExtrasConfig)
    overrides: OverridesConfig = field(default_factory=OverridesConfig)

    @property
    def default_base(self) -> str:
        return PROFILE_DEFAULT_BASE[self.profile]

    @property
    def release_branch(self) -> str:
        return PROFILE_RELEASE_BRANCH[self.profile]

    @property
    def long_lived_branches(self) -> tuple[str, ...]:
        return PROFILE_LONG_LIVED[self.profile]


@dataclass(frozen=True)
class UserConfig:
    worktree_root: Path = field(default_factory=lambda: Path("~/dev/wt").expanduser())
    # The sops-encrypted credentials file (docs/github-app.md). Encrypted, so it may be synced.
    secrets_file: str = ""

    @property
    def secrets_path(self) -> Path | None:
        return Path(self.secrets_file).expanduser() if self.secrets_file else None


def _type_name(tp: Any) -> str:
    return getattr(tp, "__name__", str(tp))


def _convert(value: Any, tp: Any, key: str, source: Path) -> Any:
    """Check `value` against the annotation `tp` and convert lists to tuples."""
    origin = typing.get_origin(tp)
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        if not isinstance(value, dict):
            raise ConfigError(f"{source}: `{key}` must be a table")
        return _build(tp, value, source, prefix=f"{key}.")
    if origin is tuple:
        (item_tp, _ellipsis) = typing.get_args(tp)
        if not isinstance(value, list):
            raise ConfigError(f"{source}: `{key}` must be a list of {_type_name(item_tp)}")
        return tuple(_convert(v, item_tp, f"{key}[{i}]", source) for i, v in enumerate(value))
    if origin is dict:
        key_tp, val_tp = typing.get_args(tp)
        if not isinstance(value, dict):
            raise ConfigError(f"{source}: `{key}` must be a table")
        return {k: _convert(v, val_tp, f'{key}."{k}"', source) for k, v in value.items()}
    if tp is Path:
        if not isinstance(value, str):
            raise ConfigError(f"{source}: `{key}` must be a path string")
        return Path(value).expanduser()
    # bool is a subclass of int: don't let `true` through as an int, or 1 as a bool.
    if tp is int and isinstance(value, bool) or not isinstance(value, tp):
        raise ConfigError(f"{source}: `{key}` must be {_type_name(tp)}, got {value!r}")
    return value


def _build(cls: type, data: dict[str, Any], source: Path, prefix: str = "") -> Any:
    hints = typing.get_type_hints(cls)
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - names)
    if unknown:
        keys = ", ".join(f"`{prefix}{k}`" for k in unknown)
        raise ConfigError(f"{source}: unknown key {keys}")
    kwargs = {k: _convert(v, hints[k], f"{prefix}{k}", source) for k, v in data.items()}
    return cls(**kwargs)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def _check_env_dir(entry: str, source: Path) -> None:
    """`[worktree].env_dirs` entries are deleted by `wt done` and excluded from its dirty check, so
    each must name one folder inside the worktree: no `.`, `..`, absolute path, pattern or magic."""
    parts = entry.replace("\\", "/").split("/")
    bad = (
        not entry.strip()
        or PureWindowsPath(entry).is_absolute()
        or PurePosixPath(entry).is_absolute()
        or entry.startswith(("/", "\\"))
        or any(c in entry for c in "*?[]:")
        or any(p in ("", ".", "..", ".git") or p != p.strip() for p in parts)
    )
    if bad:
        raise ConfigError(
            f"{source}: `worktree.env_dirs` entry {entry!r} must be a plain relative folder name "
            "such as `.venv`: no `.` or `..`, no absolute path, no `*`/`?`/`[`/`:`, not `.git`"
        )


def _check_extra_file(entry: ExtraFile, source: Path) -> None:
    """`[project].extra_files` ends up in `release-please-config.json`, so each must be a plain
    relative path (release-please resolves it from the repo root) and a type it understands."""
    where = f"{source}: `project.extra_files` entry {entry.path!r}"
    path = entry.path.replace("\\", "/")
    parts = path.split("/")
    if (
        not entry.path.strip()
        or PureWindowsPath(entry.path).is_absolute()
        or path.startswith("/")
        or any(p in ("", ".", "..") for p in parts)
        or any(c in path for c in "*?[]")
    ):
        raise ConfigError(
            f"{where} must be a plain relative file path: no absolute path, `.` or `..`, or pattern"
        )
    if entry.type not in EXTRA_FILE_TYPES:
        known = ", ".join(f'"{t}"' for t in EXTRA_FILE_TYPES)
        raise ConfigError(f"{where} has type {entry.type!r}: it must be one of {known}")
    if entry.type == "generic" and entry.jsonpath:
        raise ConfigError(f"{where}: `jsonpath` only applies to json, toml and yaml files")
    if entry.type != "generic" and not entry.jsonpath.startswith("$"):
        raise ConfigError(f'{where} needs a `jsonpath` to the version, such as "$.version"')


def parse_repo_config(data: dict[str, Any], source: Path) -> RepoConfig:
    config: RepoConfig = _build(RepoConfig, data, source)
    if config.profile not in PROFILE_DEFAULT_BASE:
        known = ", ".join(PROFILE_DEFAULT_BASE)
        raise ConfigError(f"{source}: unknown profile {config.profile!r} (known: {known})")
    if config.checks.pr_title not in PR_TITLE_RULES:
        known = ", ".join(f'"{rule}"' for rule in PR_TITLE_RULES)
        raise ConfigError(f"{source}: `checks.pr_title` must be one of {known}")
    for extra in config.project.extra_files:
        _check_extra_file(extra, source)
    for entry in config.worktree.env_dirs:
        _check_env_dir(entry, source)
    for path, mode in config.overrides.files.items():
        if mode != "unmanaged":
            raise ConfigError(f'{source}: `overrides.files."{path}"` must be "unmanaged"')
    return config


def parse_repo_config_text(text: str, source: Path) -> RepoConfig:
    """Parse `.repo-kit.toml` text that didn't come from a local file, such as from the API."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{source}: {exc}") from exc
    return parse_repo_config(data, source)


def load_repo_config(repo_root: Path) -> RepoConfig:
    """Read `.repo-kit.toml` at `repo_root`; a missing file means every default."""
    path = repo_root / REPO_CONFIG_NAME
    if not path.is_file():
        return RepoConfig()
    return parse_repo_config(_read_toml(path), path)


def user_config_path() -> Path:
    env = os.environ.get(USER_CONFIG_ENV)
    if env:
        return Path(env).expanduser()
    return Path("~/.config/repo-kit/config.toml").expanduser()


def synced_folder(path: Path) -> str | None:
    """Return the synced folder `path` lies in (OneDrive, Dropbox, iCloud), or None."""
    resolved = path.expanduser().resolve()
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(var)
        if root and resolved.is_relative_to(Path(root).resolve()):
            return root
    for part in resolved.parts:
        if part.lower().startswith(_SYNCED_PREFIXES):
            return part
    return None


def _pr_bot_value(table: object, key: str, placeholder: str) -> str:
    """One `[pr_bot]` value for the migration command, or a placeholder if it's unusable."""
    value = table.get(key) if isinstance(table, dict) else None
    if not isinstance(value, str) or not value.strip() or "BEGIN" in value:
        return placeholder  # never echo key material, only a path
    return f'"{value}"' if " " in value else value


def _pr_bot_migration(table: object, source: Path) -> str:
    client_id = _pr_bot_value(table, "client_id", "<client_id>")
    pem = _pr_bot_value(table, "private_key", "<path to gth-pr-bot .pem>")
    return (
        f"{source}: `[pr_bot]` is replaced by the encrypted credentials file. To move:\n"
        f"  1. Delete the `[pr_bot]` table from {source}.\n"
        "  2. rk secrets init\n"
        f"  3. rk secrets set-app pr-bot --client-id {client_id} --pem {pem}\n"
        "  4. rk secrets set-app release-bot --client-id <client_id> --pem <path to .pem>\n"
        "  5. rk secrets show, and once it lists both Apps, delete the .pem files.\n"
        "See docs/github-app.md#moving-from-pr_bot."
    )


def parse_user_config(data: dict[str, Any], source: Path) -> UserConfig:
    if "pr_bot" in data:
        raise ConfigError(_pr_bot_migration(data["pr_bot"], source))
    config: UserConfig = _build(UserConfig, data, source)
    synced = synced_folder(config.worktree_root)
    if synced:
        raise ConfigError(
            f"{source}: `worktree_root` ({config.worktree_root}) is inside a synced folder "
            f"({synced}). Venvs and Git internals don't survive syncing: choose a local path."
        )
    return config


def load_user_config(path: Path | None = None) -> UserConfig:
    """Read the user config (`REPO_KIT_CONFIG`, else `~/.config/repo-kit/config.toml`)."""
    path = path or user_config_path()
    data = _read_toml(path) if path.is_file() else {}
    return parse_user_config(data, path)
