"""`rk install`, `rk configure`, `rk bootstrap` and `rk upgrade`.

`install` adopts a repo: it seeds `.repo-kit.toml`, syncs the shared files, adds the plugin
marketplace, and creates the profile's base branch when only the release branch exists.
`configure` enrols a repo for `gth-pr-bot`: the labels update-prs uses, and the App's Client ID and
private key as an Actions variable and secret. `bootstrap` runs both, then `doctor`. `upgrade`
moves the version pin to the running rk's release and re-renders; templates new in that release
are added. See docs/adoption.md.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tomllib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import repo_kit
from repo_kit import files, git
from repo_kit.config import (
    REPO_CONFIG_NAME,
    ProjectConfig,
    RepoConfig,
    UpdatePrsConfig,
    UserConfig,
    load_repo_config,
    load_user_config,
    user_config_path,
)
from repo_kit.errors import RkError
from repo_kit.github import GhCliGitHub, GitHub, NoGitHub, RepoSetup

SETTINGS_PATH = ".claude/settings.json"
MARKETPLACE = "repo-kit"
MARKETPLACE_SOURCE = {"source": "github", "repo": "GreenMachine582/repo-kit"}
PLUGIN = "repo-kit@repo-kit"  # <plugin>@<marketplace>

CLIENT_ID_VARIABLE = "GTH_PR_BOT_CLIENT_ID"
PRIVATE_KEY_SECRET = "GTH_PR_BOT_PRIVATE_KEY"  # noqa: S105  # the secret's name, not its value
INSTALLATIONS_URL = "https://github.com/settings/installations"

# Colour and description for the labels rk knows; any other configured label gets the default.
_KNOWN_LABELS = {
    "needs-merge": ("d93f0b", "The base can't be merged in automatically: run rk wt sync"),
    "wip": ("fbca04", "A ready PR under rework: update-prs leaves it alone"),
    "no-auto-update": ("c5def5", "update-prs leaves this PR alone"),
}
_DEFAULT_LABEL = ("ededed", "Used by repo-kit's update-prs")


def label_specs(config: UpdatePrsConfig) -> list[tuple[str, str, str]]:
    """(name, colour, description) for each label update-prs uses, conflict label first."""
    names = dict.fromkeys([config.conflict_label, *config.skip_labels])
    return [(name, *_KNOWN_LABELS.get(name, _DEFAULT_LABEL)) for name in names]


def read_private_key(user_config: UserConfig, source: Path) -> str:
    """The PR bot's key, checked before anything is written."""
    bot = user_config.pr_bot
    path = bot.key_path
    if not bot.client_id or path is None:
        raise RkError(
            f"{source} needs gth-pr-bot's credentials before rk can enrol a repo. Add:\n\n"
            "[pr_bot]\n"
            'client_id = "<the App\'s Client ID>"\n'
            'private_key = "~/.ssh/gth-pr-bot.pem"   # a local path, never a synced folder'
        )
    if not path.is_file():
        raise RkError(f"`pr_bot.private_key` in {source} points at {path}, which doesn't exist")
    text = path.read_text(encoding="utf-8")
    if "-----BEGIN" not in text or "PRIVATE KEY-----" not in text:
        raise RkError(f"{path} doesn't look like a PEM private key (the App's downloaded .pem)")
    return text


def configure(
    setup: RepoSetup,
    repo_config: RepoConfig,
    user_config: UserConfig,
    *,
    source: Path,
    changed: list[str],
    dry_run: bool = False,
    replace_key: bool = False,
    say: Callable[[str], None] = print,
) -> None:
    """Enrol a repo for gth-pr-bot. Safe to re-run: only what's missing or different is written."""
    key = read_private_key(user_config, source)
    client_id = user_config.pr_bot.client_id

    existing = set(setup.labels())
    for name, color, description in label_specs(repo_config.update_prs):
        if name in existing:
            continue
        if not dry_run:
            setup.create_label(name, color, description)
            changed.append(f"created label {name}")
        else:
            say(f"would create label {name}")

    if setup.variables().get(CLIENT_ID_VARIABLE) != client_id:
        if not dry_run:
            setup.set_variable(CLIENT_ID_VARIABLE, client_id)
            changed.append(f"set variable {CLIENT_ID_VARIABLE}")
        else:
            say(f"would set variable {CLIENT_ID_VARIABLE}")

    # A secret can't be read back, so it's only written when missing or when asked to replace it.
    has_key = PRIVATE_KEY_SECRET in setup.secret_names()
    if has_key and not replace_key:
        say(f"secret {PRIVATE_KEY_SECRET} is already set (--replace-key replaces it)")
    elif dry_run:
        say(f"would {'replace' if has_key else 'set'} secret {PRIVATE_KEY_SECRET}")
    else:
        setup.set_secret(PRIVATE_KEY_SECRET, key)
        changed.append(f"{'replaced' if has_key else 'set'} secret {PRIVATE_KEY_SECRET}")

    say(
        f"rk can't check the App is installed on this repo: confirm it at {INSTALLATIONS_URL}, "
        "then run the update-prs caller with dry-run ticked to confirm the credentials work."
    )
    say("`rk github apply` (rulesets and settings) is not part of configure yet (Phase 1D).")


def run_configure(cwd: Path, *, changed: list[str], dry_run: bool, replace_key: bool) -> None:
    """The command: enrol the repo whose `origin` is in `cwd`."""
    git.require_version(cwd)
    main = git.main_checkout(cwd)
    url = git.remote_url(main)
    if not git.is_github_url(url):
        raise RkError(f"origin isn't on GitHub ({url}), so there's nothing to configure")
    owner, repo = git.parse_owner_repo(url)
    configure(
        GhCliGitHub(owner, repo),
        load_repo_config(main),
        load_user_config(),
        source=user_config_path(),
        changed=changed,
        dry_run=dry_run,
        replace_key=replace_key,
    )


# --- install -------------------------------------------------------------------------------


def detect_release_type(repo_root: Path) -> str:
    """The release-please release type a fresh `.repo-kit.toml` starts with."""
    if (repo_root / "pyproject.toml").is_file():
        return "python"
    if (repo_root / "package.json").is_file():
        return "node"
    return "simple"


def seed_config(repo_root: Path, repo: str, profile: str) -> RepoConfig:
    """The config a repo without `.repo-kit.toml` starts with, pinned to the running rk."""
    engine = repo_kit.__version__
    if engine == files.UNRELEASED:
        raise RkError(
            f"this rk is an unreleased build, so there is no release to pin in {REPO_CONFIG_NAME}. "
            "Install a release (`uv tool install git+https://github.com/GreenMachine582/repo-kit"
            f'@vX.Y.Z`), or write {REPO_CONFIG_NAME} with `version = "X.Y.Z"` yourself first.'
        )
    project = ProjectConfig(name=repo, release_type=detect_release_type(repo_root))
    return RepoConfig(version=engine, profile=profile, project=project)


def plan_plugin_settings(repo_root: Path) -> str | None:
    """The new text of `.claude/settings.json`, or None when nothing needs adding.

    Only the repo-kit marketplace and plugin are added, and only where missing: a source or an
    on/off choice the repo already made is kept, as is every other key and their order.
    """
    current, problem = files.read_target(repo_root, SETTINGS_PATH)
    if problem:
        raise RkError(f"{SETTINGS_PATH} {problem}, so the plugin marketplace can't be added")
    data: Any = {}
    if current is not None:
        try:
            data = json.loads(current)
        except json.JSONDecodeError as exc:
            raise RkError(f"{SETTINGS_PATH} isn't valid JSON ({exc}): fix it and re-run") from exc
    if not isinstance(data, dict):
        raise RkError(f"{SETTINGS_PATH} must hold a JSON object")
    before = json.dumps(data)
    for key, name, value in (
        ("extraKnownMarketplaces", MARKETPLACE, {"source": dict(MARKETPLACE_SOURCE)}),
        ("enabledPlugins", PLUGIN, True),
    ):
        table = data.setdefault(key, {})
        if not isinstance(table, dict):
            raise RkError(f"{SETTINGS_PATH}: `{key}` must be an object")
        table.setdefault(name, value)
    if json.dumps(data) == before:
        return None
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def write_plugin_settings(repo_root: Path, text: str, changed: list[str]) -> None:
    target = repo_root / SETTINGS_PATH
    if target.is_file():
        backup = files.new_backup_root(repo_root) / SETTINGS_PATH
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        changed.append(f"backed up {SETTINGS_PATH} to {backup}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    changed.append(f"added the repo-kit plugin marketplace to {SETTINGS_PATH}")


HOOK_MARKER = "# Installed by `rk install --hooks`"
HOOK_TEXT = f"""#!/bin/sh
{HOOK_MARKER}: repo-kit's commit rules (checks.py).
if ! command -v rk >/dev/null 2>&1; then
  echo "rk isn't on PATH: commit-msg check skipped" >&2
  exit 0
fi
exec rk check commit-msg "$1"
"""


def plan_hook(repo_root: Path) -> Path | None:
    """Where the commit-msg hook must be written, or None if it's already there.

    Refuses, before anything is written, when another tool's hook is in the way.
    """
    path = git.git_path(repo_root, "hooks/commit-msg")
    if not path.is_file():
        return path
    current = files.lf(path.read_text(encoding="utf-8", errors="replace"))
    if current == HOOK_TEXT:
        return None
    if HOOK_MARKER not in current:
        raise RkError(
            f"{path} is another tool's commit-msg hook, so rk won't replace it. Add "
            '`rk check commit-msg "$1"` to it by hand, or remove it and re-run '
            "`rk install --hooks`."
        )
    return path


def write_hook(repo_root: Path, path: Path, changed: list[str]) -> None:
    if path.is_file():
        backup = files.new_backup_root(repo_root) / "hooks" / path.name
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        changed.append(f"backed up the old commit-msg hook to {backup}")
        verb = "updated"
    else:
        verb = "installed"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HOOK_TEXT, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    changed.append(f"{verb} the commit-msg hook at {path}")


def base_branch_missing(repo_root: Path, config: RepoConfig) -> bool:
    """Whether origin lacks the profile's base branch. Refuses if it can't be created either."""
    git.fetch(repo_root)
    base, release = config.default_base, config.release_branch
    if git.remote_branch_exists(repo_root, base):
        return False
    if not git.remote_branch_exists(repo_root, release):
        raise RkError(
            f"origin has neither `{base}` nor `{release}`: the {config.profile} profile creates "
            f"`{base}` from `{release}`, so push `{release}` first"
        )
    return True


def create_base_branch(
    repo_root: Path, config: RepoConfig, changed: list[str], say: Callable[[str], None]
) -> None:
    base, release = config.default_base, config.release_branch
    git.create_remote_branch(repo_root, base, f"refs/remotes/origin/{release}")
    changed.append(f"created `{base}` on origin from `{release}`")
    git.fetch(repo_root)
    say(
        f"`{base}` isn't the default branch yet: that is a repo setting, which "
        "`rk github apply` will set (Phase 1D). Until then, change it in the repo's settings."
    )


def retarget_prs(
    github: GitHub,
    config: RepoConfig,
    *,
    changed: list[str],
    confirm: Callable[[str], bool],
    say: Callable[[str], None],
) -> None:
    """Offer to move open PRs off the release branch, which only takes release PRs.

    It looks at the PRs as they are now, not at whether the base branch was just created, so a
    run that was declined or cut short can be finished by running install again.
    """
    base, release = config.default_base, config.release_branch
    prs = [pr for pr in github.list_open_prs() if pr.base_ref == release and pr.head_ref != base]
    if not prs:
        return
    say(f"Open PRs that target `{release}`, which only takes release PRs from `{base}`:")
    for pr in prs:
        say(f"  #{pr.number}  {pr.head_ref}")
    if not confirm(f"Retarget these {len(prs)} to `{base}`?"):
        say("Left as they are. Run `rk install` again to retarget them.")
        return
    for pr in prs:
        github.retarget(pr.number, base)
        changed.append(f"retargeted #{pr.number} ({pr.head_ref}) to `{base}`")


def ask(question: str) -> bool:
    """Yes only when a person types it: with no terminal to ask on, the answer is no."""
    if not sys.stdin.isatty():
        print(f"{question} Not asked: there is no terminal to answer on.")
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def install(
    repo_root: Path,
    owner: str,
    repo: str,
    github: GitHub,
    *,
    profile: str,
    changed: list[str],
    hooks: bool = False,
    confirm: Callable[[str], bool] = ask,
    say: Callable[[str], None] = print,
    templates: Path | None = None,
) -> RepoConfig:
    """Adopt the repo at `repo_root`. Safe to re-run: only what's missing is written.

    Everything that can refuse is checked before the first write, and the working tree is
    written before anything on origin or GitHub changes.
    """
    if (repo_root / REPO_CONFIG_NAME).is_file():
        config = load_repo_config(repo_root)
    else:
        # `.repo-kit.toml` is itself a seeded template, so the sync below creates it.
        config = seed_config(repo_root, repo, profile)
    files.require_pin(config)
    plan = files.plan_sync(repo_root, config, owner, repo, templates)
    say(files.summary(plan))
    files.raise_if_refused(plan)
    settings = plan_plugin_settings(repo_root)
    hook = plan_hook(repo_root) if hooks else None
    create_base = base_branch_missing(repo_root, config)

    files.apply_plan(repo_root, plan, changed)
    if settings is not None:
        write_plugin_settings(repo_root, settings, changed)
    if hook is not None:
        write_hook(repo_root, hook, changed)
    if create_base:
        create_base_branch(repo_root, config, changed, say)
    retarget_prs(github, config, changed=changed, confirm=confirm, say=say)
    return config


def _origin(cwd: Path) -> tuple[Path, str, str, str]:
    """The checkout rk was run in, its origin URL, and the owner and repo that names."""
    git.require_version(cwd)
    repo_root = git.toplevel(cwd)
    url = git.remote_url(repo_root)
    owner, repo = git.parse_owner_repo(url)
    return repo_root, url, owner, repo


def run_install(cwd: Path, *, profile: str, changed: list[str], hooks: bool = False) -> None:
    """The command: adopt the checkout `cwd` is in."""
    repo_root, url, owner, repo = _origin(cwd)
    github: GitHub = GhCliGitHub(owner, repo) if git.is_github_url(url) else NoGitHub()
    install(repo_root, owner, repo, github, profile=profile, changed=changed, hooks=hooks)


# --- upgrade -------------------------------------------------------------------------------

_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
_PIN_LINE = re.compile(r"^(\s*version\s*=\s*)([\"'])([^\"'\r\n]*)\2(.*)$", re.DOTALL)
_PIN_KEY = re.compile(r"^\s*version\s*=")


def parse_version(text: str) -> tuple[int, int, int]:
    """`X.Y.Z` or `vX.Y.Z` as numbers. Anything else is refused."""
    match = _VERSION_RE.fullmatch(text.strip())
    if not match:
        raise RkError(f"`{text}` isn't a release: expected vX.Y.Z or X.Y.Z, such as v1.2.3")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def set_pin_text(text: str, version: str) -> str:
    """`text` with its one top-level `version` line moved to `version`, all else kept."""
    lines = text.splitlines(keepends=True)
    found: list[int] = []
    for number, line in enumerate(lines):
        if line.lstrip().startswith("["):
            break  # the top-level keys end at the first table
        if _PIN_KEY.match(line):
            found.append(number)
    if len(found) != 1:
        raise RkError(
            f'{REPO_CONFIG_NAME} must have exactly one top-level `version = "X.Y.Z"` line '
            f"(found {len(found)}): fix it by hand, then re-run"
        )
    match = _PIN_LINE.match(lines[found[0]])
    if not match:
        raise RkError(
            f"the `version` line in {REPO_CONFIG_NAME} isn't a plain quoted string: "
            'write it as `version = "X.Y.Z"`, then re-run'
        )
    head, quote, _old, rest = match.groups()
    lines[found[0]] = f"{head}{quote}{version}{quote}{rest}"
    new_text = "".join(lines)
    # The line scan doesn't know TOML (a multi-line string can hold a `version = …` line), so
    # check the result parses to the new pin before anything is written.
    try:
        parsed = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        raise RkError(
            f"moving the pin would leave {REPO_CONFIG_NAME} invalid ({exc}): "
            'write the pin as a plain top-level `version = "X.Y.Z"` line, then re-run'
        ) from None
    if parsed.get("version") != version:
        raise RkError(
            f"couldn't move the pin in {REPO_CONFIG_NAME} safely: the line rk found isn't its "
            'top-level `version` key. Write it as a plain `version = "X.Y.Z"` line, then re-run'
        )
    return new_text


def upgrade(
    repo_root: Path,
    owner: str,
    repo: str,
    target: str | None,
    *,
    changed: list[str],
    say: Callable[[str], None] = print,
    templates: Path | None = None,
) -> None:
    """Move the pin to `target` (the running rk's release) and re-render the shared files.

    Everything that can refuse is checked, and the sync planned, before the first write.
    """
    engine = repo_kit.__version__
    if target is None:
        if engine == files.UNRELEASED:
            raise RkError(
                "this rk is an unreleased build, so it has no release to upgrade to: "
                "give the version explicitly, such as `rk upgrade v1.2.3`"
            )
        target = engine
    wanted = parse_version(target)
    version = ".".join(str(n) for n in wanted)
    if engine == files.UNRELEASED:
        say(
            f"warning: this rk is an unreleased build, so the files rendered now are this build's "
            f"templates, which may differ from release {version}. CI's pinned engine may then "
            f"report drift: prefer installing rk {version} and upgrading with it."
        )
    elif version != engine:
        raise RkError(
            f"can't upgrade to {version}: this rk is {engine}, and the templates come with the "
            f"engine. Install rk {version} first, then run `rk upgrade`."
        )

    config = load_repo_config(repo_root)
    if not config.version:
        raise RkError(f"`version` is empty in {REPO_CONFIG_NAME}")
    try:
        current = parse_version(config.version)
    except RkError:
        raise RkError(
            f"the current pin in {REPO_CONFIG_NAME}, `{config.version}`, isn't a release: "
            'fix it by hand to `version = "X.Y.Z"`, then re-run'
        ) from None
    if wanted < current:
        raise RkError(
            f"can't downgrade from {config.version} to {version}: rk upgrade only moves forward"
        )
    if wanted == current:
        say(f"already at {version}")

    config_path = repo_root / REPO_CONFIG_NAME
    old_text = config_path.read_bytes().decode("utf-8")  # not read_text: keep CRLF as written
    new_text = set_pin_text(old_text, version)

    plan = files.plan_sync(repo_root, replace(config, version=version), owner, repo, templates)
    say(files.summary(plan))
    files.raise_if_refused(plan)

    # The pin is written last: if applying the plan fails, the old pin stays and a re-run finishes.
    backup_root = files.new_backup_root(repo_root)
    files.apply_plan(repo_root, plan, changed, backup_root=backup_root)
    if new_text != old_text:
        backup = backup_root / REPO_CONFIG_NAME
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config_path, backup)
        config_path.write_text(new_text, encoding="utf-8", newline="")
        changed.append(f"moved the version pin in {REPO_CONFIG_NAME} to {version}")
        changed.append(f"backed up the old {REPO_CONFIG_NAME} to {backup}")
    say(
        "Migration notes aren't built yet (Phase 1D): see the release notes at "
        "https://github.com/GreenMachine582/repo-kit/releases. `rk github diff` also comes in "
        "Phase 1D. Renamed or removed templates are left in place: move them by hand."
    )


def run_upgrade(cwd: Path, version: str | None, *, changed: list[str]) -> None:
    """The command: move the pin of the checkout `cwd` is in."""
    repo_root, _url, owner, repo = _origin(cwd)
    upgrade(repo_root, owner, repo, version, changed=changed)


def bootstrap(
    repo_root: Path,
    owner: str,
    repo: str,
    github: GitHub,
    setup: RepoSetup,
    user_config: UserConfig,
    *,
    source: Path,
    profile: str,
    changed: list[str],
    confirm: Callable[[str], bool] = ask,
    say: Callable[[str], None] = print,
    templates: Path | None = None,
) -> None:
    """`install`, then `configure`, then `doctor`. The PR bot's key is read first, so a machine
    that can't enrol the repo stops before anything is written."""
    from repo_kit import doctor  # here, not at the top: doctor imports this module

    read_private_key(user_config, source)
    config = install(
        repo_root,
        owner,
        repo,
        github,
        profile=profile,
        changed=changed,
        confirm=confirm,
        say=say,
        templates=templates,
    )
    configure(setup, config, user_config, source=source, changed=changed, say=say)
    checks = doctor.doctor(
        repo_root,
        owner,
        repo,
        github,
        setup,
        user_source=source,
        user_config=user_config,
        templates=templates,
    )
    say(doctor.render(checks))
    if doctor.failed(checks):
        say("Fix the [FAIL] items above, then re-run `rk doctor`.")


def run_bootstrap(cwd: Path, *, profile: str, changed: list[str]) -> None:
    """The command: install, then enrol the repo for gth-pr-bot."""
    repo_root, url, owner, repo = _origin(cwd)
    if not git.is_github_url(url):
        raise RkError(
            f"origin isn't on GitHub ({url}), so there's nothing to configure: use `rk install`"
        )
    client = GhCliGitHub(owner, repo)
    bootstrap(
        repo_root,
        owner,
        repo,
        client,
        client,
        load_user_config(),
        source=user_config_path(),
        profile=profile,
        changed=changed,
    )
