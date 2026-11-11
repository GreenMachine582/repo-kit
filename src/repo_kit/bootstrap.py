"""`rk install`, `rk configure`, `rk bootstrap` and `rk upgrade`.

`configure` enrols a repo for `gth-pr-bot`: the labels update-prs uses, and the App's Client ID and
private key as an Actions variable and secret. The rest is Phase 1C. See docs/adoption.md.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from repo_kit import git
from repo_kit.config import (
    RepoConfig,
    UpdatePrsConfig,
    UserConfig,
    load_repo_config,
    load_user_config,
    user_config_path,
)
from repo_kit.errors import RkError
from repo_kit.github import GhCliGitHub, RepoSetup

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
