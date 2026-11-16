"""`rk doctor`: read-only health check.

Tools, both config files, the credentials file, the managed files, the plugin settings, the base
branch, both Apps' enrolment and stale worktrees. It writes nothing and doesn't fetch: remote
branches are read as of the last fetch. Repo settings and rulesets are compared with what the
config describes; the latest release comes later. See docs/adoption.md.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import repo_kit
from repo_kit import bootstrap, files, git, github_config
from repo_kit.config import (
    REPO_CONFIG_NAME,
    RepoConfig,
    UserConfig,
    load_repo_config,
    load_user_config,
    user_config_path,
)
from repo_kit.credentials import (
    APPS,
    MIN_SOPS,
    PR_BOT,
    Credentials,
    Sops,
    SopsCli,
    apps_for,
    conflict_copy_warning,
    key_fingerprint,
    load_credentials,
)
from repo_kit.errors import RkError
from repo_kit.github import GhCliGitHub, GitHub, NoGitHub, RepoSetup
from repo_kit.worktree import pr_state, prs_for, repo_dir, rk_worktrees

MIN_GH = (2, 40)
MIN_PYTHON = (3, 11)


class Status(StrEnum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass(frozen=True)
class Check:
    status: Status
    label: str
    detail: str = ""


def _ok(label: str, detail: str = "") -> Check:
    return Check(Status.OK, label, detail)


def _warn(label: str, detail: str = "") -> Check:
    return Check(Status.WARN, label, detail)


def _fail(label: str, detail: str = "") -> Check:
    return Check(Status.FAIL, label, detail)


def _skip(label: str, detail: str = "") -> Check:
    return Check(Status.SKIP, label, detail)


def _dotted(version: tuple[int, ...]) -> str:
    return ".".join(map(str, version))


# --- tools ---------------------------------------------------------------------------------


def parse_gh_version(text: str) -> tuple[int, int]:
    """`gh version 2.63.0 (2024-11-27)` -> (2, 63)."""
    match = re.search(r"gh version (\d+)\.(\d+)", text)
    if not match:
        raise RkError(f"can't read the gh version from {text!r}")
    return int(match[1]), int(match[2])


def installed_gh_version() -> tuple[int, int] | None:
    """The `gh` on PATH, or None if there isn't one."""
    try:
        proc = subprocess.run(
            ["gh", "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError:
        return None
    return parse_gh_version(proc.stdout)


def check_tools(
    cwd: Path,
    gh_version: Callable[[], tuple[int, int] | None],
    sops_version: Callable[[], tuple[int, int] | None],
) -> Check:
    found: list[str] = []
    problems: list[str] = []
    failed = False
    try:
        have_git = git.version(cwd)
        found.append(f"git {_dotted(have_git)}")
        if have_git < git.MIN_GIT:
            problems.append(f"rk needs Git {_dotted(git.MIN_GIT)} or newer")
            failed = True
    except RkError as exc:
        found.append("git ?")
        problems.append(str(exc))
        failed = True
    try:
        have_gh = gh_version()
    except RkError as exc:
        have_gh = None
        problems.append(str(exc))
    if have_gh is None:
        found.append("gh missing")
        if not problems:
            problems.append(
                "install the GitHub CLI and `gh auth login`: `rk wt`, `configure` and "
                "`update-prs` need it"
            )
    else:
        found.append(f"gh {_dotted(have_gh)}")
        if have_gh < MIN_GH:
            problems.append(f"rk needs gh {_dotted(MIN_GH)} or newer")
            failed = True
    have_sops = sops_version()
    if have_sops is None:
        found.append("sops missing")
        problems.append("install sops and age: `rk configure` and `rk secrets` need them")
    else:
        found.append(f"sops {_dotted(have_sops)}")
        if have_sops < MIN_SOPS:
            problems.append(f"`rk configure` and `rk secrets` need sops {_dotted(MIN_SOPS)}+")
    py = sys.version_info[:2]
    found.append(f"Python {_dotted(py)}")
    if py < MIN_PYTHON:
        problems.append(f"rk needs Python {_dotted(MIN_PYTHON)} or newer")
        failed = True
    label = " / ".join(found)
    if failed:
        return _fail(label, "\n".join(problems))
    return _warn(label, "\n".join(problems)) if problems else _ok(label)


# --- config --------------------------------------------------------------------------------


def check_repo_config(repo_root: Path) -> tuple[Check, RepoConfig | None, bool]:
    """The check, the config if it loaded, and whether its pin matches this rk's templates."""
    if not (repo_root / REPO_CONFIG_NAME).is_file():
        return _fail(f"{REPO_CONFIG_NAME} missing", "run `rk install`"), None, False
    try:
        config = load_repo_config(repo_root)
    except RkError as exc:
        return _fail(f"{REPO_CONFIG_NAME} invalid", str(exc)), None, False
    label = f"{REPO_CONFIG_NAME} valid (profile {config.profile}, version {config.version})"
    if not config.version:
        return _fail(f"{REPO_CONFIG_NAME}: `version` is empty"), config, False
    try:
        bootstrap.parse_version(config.version)
    except RkError:
        detail = f'`{config.version}` isn\'t a release: fix it to `version = "X.Y.Z"`'
        return _fail(f"{REPO_CONFIG_NAME}: bad version", detail), config, False
    engine = repo_kit.__version__
    if engine == files.UNRELEASED:
        detail = "this rk is an unreleased build: files are compared with its own templates"
        return _ok(label, detail), config, True
    if engine != config.version:
        detail = (
            f"this rk is {engine}: run `rk upgrade` to move the pin, or install rk "
            f"{config.version}. The files can't be checked until the two match."
        )
        return _warn(label, detail), config, False
    # "latest X.Y.Z" needs a published release to compare against: Phase 1D.
    return _ok(label), config, True


def check_user_config(source: Path) -> tuple[Check, UserConfig | None]:
    try:
        config = load_user_config(source)
    except RkError as exc:
        return _fail("user config invalid", str(exc)), None
    where = str(source) if source.is_file() else f"{source} (not found, so the defaults)"
    return _ok("user config valid", where), config


def check_credentials(
    user_config: UserConfig | None, source: Path, sops: Sops, config: RepoConfig | None = None
) -> tuple[Check, Credentials | None]:
    """The Apps this repo uses (every App without a valid repo config) must decrypt."""
    if user_config is None:
        return _skip("credentials file", "need a valid user config"), None
    apps = apps_for(config) if config else APPS
    path = user_config.secrets_path
    conflict = conflict_copy_warning(path) if path else None
    try:
        credentials = load_credentials(user_config, source, sops, apps)
    except RkError as exc:
        detail = f"{exc}\n{conflict}" if conflict else str(exc)
        return _warn("credentials file can't be read", detail), None
    names = ", ".join(app.name for app in apps)
    if conflict:
        return _warn(f"credentials file: {names}, with a conflict copy", conflict), credentials
    return _ok(f"credentials file: {names}", str(path)), credentials


# --- files ---------------------------------------------------------------------------------

_OUT_OF_SYNC = {
    files.Action.CREATE: "missing",
    files.Action.UPDATE: "differs from the render",
    files.Action.ADOPT: "matches but isn't in the lock",
    files.Action.REFUSE: "edited by hand",
}


def check_files(
    repo_root: Path, config: RepoConfig, owner: str, repo: str, templates: Path | None
) -> list[Check]:
    try:
        plan = files.plan_sync(repo_root, config, owner, repo, templates)
    except RkError as exc:
        return [_fail("managed files can't be checked", str(exc))]
    drifted = [f for f in plan.files if f.action in _OUT_OF_SYNC]
    in_sync = [f for f in plan.files if f.action is files.Action.UNCHANGED]
    label = f"managed files: {len(in_sync)} in sync, {len(drifted)} drifted"
    checks: list[Check] = []
    if drifted:
        lines = [
            f"{f.path}: {f.detail if f.action is files.Action.REFUSE else _OUT_OF_SYNC[f.action]}"
            for f in drifted
        ]
        lines.append("`rk sync --check` shows the diffs; `rk sync` fixes all but hand edits")
        checks.append(_fail(label, "\n".join(lines)))
    elif plan.lock_changed:
        checks.append(_fail(label, f"{files.LOCK_NAME} is out of date: run `rk sync`"))
    else:
        checks.append(_ok(label))
    for f in plan.files:
        if f.action is files.Action.ORPHAN:
            checks.append(_warn(f"not a template: {f.path}", f.detail))
    return checks


_WORKFLOW_CALL = re.compile(r"^\s+workflow_call:", re.MULTILINE)


def _is_reusable_workflow(path: Path) -> bool:
    """A `workflow_call:` trigger: the workflow itself, which a generated caller never is."""
    try:
        return bool(_WORKFLOW_CALL.search(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError):
        return False


def check_overrides(repo_root: Path, config: RepoConfig) -> list[Check]:
    """WARN for each override, except on a reusable workflow: in repo-kit the callers' paths are
    the workflows themselves, so those overrides are expected."""
    checks: list[Check] = []
    for path, mode in sorted(config.overrides.files.items()):
        if _is_reusable_workflow(repo_root / path):
            checks.append(
                _ok(f"override: {path} is {mode}", "a reusable workflow itself, not a caller")
            )
        else:
            checks.append(_warn(f"override in use: {path} is {mode}"))
    return checks


def check_plugin(repo_root: Path) -> Check:
    try:
        missing = bootstrap.plan_plugin_settings(repo_root) is not None
    except RkError as exc:
        return _fail(f"plugin: {bootstrap.SETTINGS_PATH} can't be read", str(exc))
    if missing:
        return _warn(
            f"plugin: {bootstrap.SETTINGS_PATH} lacks the repo-kit marketplace or plugin",
            "run `rk install`",
        )
    return _ok(f"plugin: repo-kit is in {bootstrap.SETTINGS_PATH}")


def check_base_branch(repo_root: Path, config: RepoConfig) -> Check:
    base = config.default_base
    if git.remote_branch_exists(repo_root, base):
        return _ok(f"base branch: origin/{base} exists (as of the last fetch)")
    return _fail(
        f"base branch: origin/{base} missing (as of the last fetch)",
        "`git fetch` if it was created elsewhere; otherwise `rk install` creates it",
    )


# --- GitHub --------------------------------------------------------------------------------


def check_enrolment(
    setup: RepoSetup, config: RepoConfig, credentials: Credentials | None
) -> list[Check]:
    """One check per App. Client IDs are compared only when the credentials file decrypted."""
    try:
        labels = set(setup.labels())
        variables = setup.variables()
        secrets = set(setup.secret_names())
    except RkError as exc:
        return [_warn("Apps: can't read the repo's labels, variables and secrets", str(exc))]
    checks: list[Check] = []
    for app in APPS:
        problems: list[str] = []
        if app is PR_BOT:
            if not config.update_prs.enabled:
                checks.append(_skip(f"{app.name}: update-prs is disabled in .repo-kit.toml"))
                continue
            problems += [
                f"label {name} is missing"
                for name, _color, _desc in bootstrap.label_specs(config.update_prs)
                if name not in labels
            ]
        client_id = variables.get(app.client_id_variable)
        found = credentials.get(app.key) if credentials else None
        wanted = found.client_id if found else ""
        if not client_id:
            problems.append(f"variable {app.client_id_variable} is missing")
        elif wanted and client_id != wanted:
            problems.append(
                f"variable {app.client_id_variable} isn't the Client ID in your credentials file"
            )
        enrolled = not problems
        # A secret can't be read back: its fingerprint variable says which key it holds.
        secret, fingerprint_variable = app.private_key_secret, app.key_fingerprint_variable
        fingerprint = variables.get(fingerprint_variable)
        replace_key = False
        unknown_key = ""
        if secret not in secrets:
            problems.append(f"secret {secret} is missing")
            enrolled = False
        elif not fingerprint:
            unknown_key = (
                f"variable {fingerprint_variable} is missing, so rk can't tell which key "
                f"secret {secret} holds"
            )
            replace_key = True
        elif found and fingerprint != key_fingerprint(found.private_key):
            problems.append(f"secret {secret} holds an older key than your credentials file")
            replace_key = True
        fix = "run `rk configure --replace-key`" if replace_key else "run `rk configure`"
        if problems:
            label = f"{app.name} not enrolled" if not enrolled else f"{app.name}: older key"
            lines = [*problems, unknown_key] if unknown_key else problems
            checks.append(_fail(label, "\n".join([*lines, fix])))
        elif unknown_key:
            checks.append(_warn(f"{app.name}: key unknown", f"{unknown_key}: {fix}"))
        else:
            what = "labels, " if app is PR_BOT else ""
            label = f"{app.name}: {what}Client ID variable and private key secret present"
            if found:
                label += ", key fingerprint matches"
            checks.append(_ok(label))
    return checks


def check_github(setup: RepoSetup, config: RepoConfig) -> Check:
    try:
        result = github_config.plan(config, setup)
    except RkError as exc:
        return _warn("repo settings and rulesets: can't read them", str(exc))
    if result.drift:
        lines = [*github_config.render(result), "see `rk github diff`, then `rk github apply`"]
        return _warn("repo settings or rulesets differ from .repo-kit.toml", "\n".join(lines))
    names = ", ".join(r.name for r in result.rulesets.rulesets)
    return _ok(f"repo settings and rulesets match: {names}")


def check_worktrees(
    repo_root: Path, owner: str, repo: str, github: GitHub, user_config: UserConfig
) -> list[Check]:
    ours = rk_worktrees(repo_root, repo_dir(user_config.worktree_root, owner, repo))
    try:
        prs = prs_for([branch for _, branch in ours], github)
    except RkError as exc:
        return [_warn(f"worktrees: {len(ours)}; can't look up their PRs", str(exc))]
    stale = [
        f"{path} ({branch}: PR #{pr.number} {pr_state(pr)})"
        for (path, branch), pr in zip(ours, prs, strict=True)
        if pr and pr.finished
    ]
    if not stale:
        return [_ok(f"worktrees: {len(ours)}, none stale")]
    label = f"{len(stale)} stale worktree{'s' if len(stale) > 1 else ''}"
    detail = "\n".join([*stale, "remove each with `rk wt done <branch>`"])
    return [_warn(label, detail)]


# --- the report ----------------------------------------------------------------------------


def doctor(
    repo_root: Path,
    owner: str,
    repo: str,
    github: GitHub,
    setup: RepoSetup | None,
    *,
    user_source: Path,
    user_config: UserConfig | None = None,
    templates: Path | None = None,
    gh_version: Callable[[], tuple[int, int] | None] | None = None,
    sops: Sops | None = None,
) -> list[Check]:
    """Every check, in report order. Reads only: nothing is written, fetched or pushed.

    `user_config` is the one already loaded from `user_source`, when the caller has it.
    """
    sops = sops or SopsCli()
    checks = [check_tools(repo_root, gh_version or installed_gh_version, sops.version)]

    repo_check, config, pin_matches = check_repo_config(repo_root)
    if user_config is None:
        user_check, user_config = check_user_config(user_source)
    else:
        user_check = _ok("user config valid", str(user_source))
    credentials_check, credentials = check_credentials(user_config, user_source, sops, config)
    checks += [repo_check, user_check, credentials_check]

    if config is None:
        needs = "managed files, plugin, base branch, App enrolment"
        checks.append(_skip(needs, "need a valid config"))
    else:
        if pin_matches:
            checks += check_files(repo_root, config, owner, repo, templates)
        else:
            checks.append(_skip("managed files", "need the pin to match this rk"))
        checks += check_overrides(repo_root, config)
        checks.append(check_plugin(repo_root))
        checks.append(check_base_branch(repo_root, config))
        if setup is None:
            checks.append(_skip("App enrolment", "origin isn't on GitHub"))
        else:
            checks += check_enrolment(setup, config, credentials)

    if setup is not None:
        checks.append(
            _skip(
                "Apps installed and token mint",
                f"can't be checked from here: confirm at {bootstrap.INSTALLATIONS_URL}, then "
                "run the update-prs caller with dry-run ticked",
            )
        )
    if config is None or setup is None:
        checks.append(
            _skip("repo settings and rulesets", "need a valid config and a GitHub origin")
        )
    else:
        checks.append(check_github(setup, config))

    if user_config is None:
        checks.append(_skip("worktrees", "need a valid user config"))
    else:
        checks += check_worktrees(repo_root, owner, repo, github, user_config)
    return checks


def failed(checks: list[Check]) -> bool:
    return any(c.status is Status.FAIL for c in checks)


def render(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        lines.append(f"{'[' + c.status + ']':<7}{c.label}")
        lines += [f"{'':7}{line}" for line in c.detail.splitlines()]
    return "\n".join(lines)


def run_doctor(cwd: Path, *, say: Callable[[str], None] = print) -> int:
    """The command. Returns 1 if any check failed, else 0."""
    repo_root = git.toplevel(cwd)
    url = git.remote_url(repo_root)
    owner, repo = git.parse_owner_repo(url)
    if git.is_github_url(url):
        client = GhCliGitHub(owner, repo)
        github: GitHub = client
        setup: RepoSetup | None = client
    else:
        github, setup = NoGitHub(), None
    checks = doctor(repo_root, owner, repo, github, setup, user_source=user_config_path())
    say(render(checks))
    return 1 if failed(checks) else 0
