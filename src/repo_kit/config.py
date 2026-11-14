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
from pathlib import Path
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

# Folder names of sync clients that break venvs and Git internals.
_SYNCED_PREFIXES = ("onedrive", "dropbox", "icloud")


class ConfigError(RkError):
    """A config file that can't be read, or has unknown keys or wrong types."""


@dataclass(frozen=True)
class ProjectConfig:
    name: str = ""
    release_type: str = "python"


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
class PrBotConfig:
    """`gth-pr-bot`'s credentials, used by `rk configure` to enrol a repo."""

    client_id: str = ""
    private_key: str = ""  # path to the App's .pem; "~" is expanded

    @property
    def key_path(self) -> Path | None:
        return Path(self.private_key).expanduser() if self.private_key else None


@dataclass(frozen=True)
class UserConfig:
    worktree_root: Path = field(default_factory=lambda: Path("~/dev/wt").expanduser())
    pr_bot: PrBotConfig = field(default_factory=PrBotConfig)


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


def parse_repo_config(data: dict[str, Any], source: Path) -> RepoConfig:
    config: RepoConfig = _build(RepoConfig, data, source)
    if config.profile not in PROFILE_DEFAULT_BASE:
        known = ", ".join(PROFILE_DEFAULT_BASE)
        raise ConfigError(f"{source}: unknown profile {config.profile!r} (known: {known})")
    if config.checks.pr_title not in PR_TITLE_RULES:
        known = ", ".join(f'"{rule}"' for rule in PR_TITLE_RULES)
        raise ConfigError(f"{source}: `checks.pr_title` must be one of {known}")
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


def parse_user_config(data: dict[str, Any], source: Path) -> UserConfig:
    config: UserConfig = _build(UserConfig, data, source)
    synced = synced_folder(config.worktree_root)
    if synced:
        raise ConfigError(
            f"{source}: `worktree_root` ({config.worktree_root}) is inside a synced folder "
            f"({synced}). Venvs and Git internals don't survive syncing: choose a local path."
        )
    key = config.pr_bot.key_path
    synced = synced_folder(key) if key else None
    if synced:
        raise ConfigError(
            f"{source}: `pr_bot.private_key` ({key}) is inside a synced folder ({synced}). "
            "A private key must not be copied to the cloud: move it to a local path."
        )
    return config


def load_user_config(path: Path | None = None) -> UserConfig:
    """Read the user config (`REPO_KIT_CONFIG`, else `~/.config/repo-kit/config.toml`)."""
    path = path or user_config_path()
    data = _read_toml(path) if path.is_file() else {}
    return parse_user_config(data, path)
