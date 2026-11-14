"""`rk github diff` and `rk github apply`: the repo settings and rulesets the config describes.

`diff` only reads. `apply` shows the same diff, asks, backs up the live state, writes, and reads
everything back. It never runs in CI. See docs/rulesets.md.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repo_kit import files, git, rulesets, settings
from repo_kit.config import RepoConfig, load_repo_config
from repo_kit.errors import RkError
from repo_kit.github import GhCliGitHub, RepoSetup
from repo_kit.rulesets import RulesetDiff, RulesetsDiff, State

_CI_VARIABLES = ("CI", "GITHUB_ACTIONS")


@dataclass(frozen=True)
class GithubPlan:
    settings: RulesetDiff
    rulesets: RulesetsDiff
    live_settings: dict[str, Any]
    live_rulesets: list[dict[str, Any]]

    @property
    def drift(self) -> bool:
        return self.settings.state is not State.MATCHES or self.rulesets.drift


def plan(config: RepoConfig, setup: RepoSetup) -> GithubPlan:
    """Read the live settings and rulesets once, and compare them with the config."""
    live_settings = setup.repo_settings()
    live_rulesets = setup.rulesets()
    return GithubPlan(
        settings.diff(config, live_settings),
        rulesets.diff(config, live_rulesets),
        live_settings,
        live_rulesets,
    )


def render(result: GithubPlan) -> list[str]:
    settings_only = RulesetsDiff((result.settings,))
    return [*rulesets.render(settings_only), *rulesets.render(result.rulesets)]


def run_diff(config: RepoConfig, setup: RepoSetup, *, say: Callable[[str], None] = print) -> int:
    """`rk github diff`. Read-only; 1 if anything differs."""
    result = plan(config, setup)
    for line in render(result):
        say(line)
    if result.drift:
        say("GitHub differs from .repo-kit.toml (- live, + wanted). `rk github apply` applies it.")
        return 1
    say("GitHub matches .repo-kit.toml.")
    return 0


def _in_ci() -> bool:
    return any(os.environ.get(name) for name in _CI_VARIABLES)


def _without_missing_base(
    repo_root: Path, config: RepoConfig, result: RulesetDiff, say: Callable[[str], None]
) -> RulesetDiff:
    """GitHub refuses a default branch that doesn't exist: leave that change out, and say so."""
    base = config.default_base
    if not any(c.key == "default_branch" for c in result.changes):
        return result
    if git.remote_branch_exists(repo_root, base):
        return result
    say(f"`{base}` isn't on origin yet, so the default branch is left as it is: run `rk install`.")
    kept = tuple(c for c in result.changes if c.key != "default_branch")
    return RulesetDiff(result.name, State.DRIFTED if kept else State.MATCHES, kept)


def apply(
    repo_root: Path,
    config: RepoConfig,
    setup: RepoSetup,
    *,
    target: str,
    confirm: Callable[[str], bool],
    changed: list[str],
    say: Callable[[str], None] = print,
) -> int:
    """`rk github apply`. Returns 0 when GitHub matches the config at the end, else 1."""
    if _in_ci():
        raise RkError(
            "`rk github apply` never runs in CI: run it yourself, with admin rights, after "
            "reading `rk github diff`"
        )
    before = plan(config, setup)
    for line in render(before):
        say(line)
    if not before.drift:
        say("Nothing to apply: GitHub matches .repo-kit.toml.")
        return 0

    settings_diff = _without_missing_base(repo_root, config, before.settings, say)
    to_create = [r for r in before.rulesets.rulesets if r.state is State.MISSING]
    to_update = [r for r in before.rulesets.rulesets if r.state is State.DRIFTED]
    if not (settings_diff.changes or to_create or to_update):
        say("Nothing that can be applied yet.")
        return 1
    if not confirm(f"Apply these changes to {target}?"):
        say("Nothing applied.")
        return 1

    backup = files.new_backup_root(repo_root) / "github.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    state = {"settings": before.live_settings, "rulesets": before.live_rulesets}
    backup.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    values = settings.changed_values(config, settings_diff)
    if values:
        setup.update_repo_settings(values)
        changed.append(f"updated repo settings: {', '.join(values)}")
    built = {r["name"]: r for r in rulesets.build(config)}
    live_ids: dict[str, int] = {r["name"]: r["id"] for r in before.live_rulesets}
    for r in to_create:
        setup.create_ruleset(built[r.name])
        changed.append(f"created ruleset `{r.name}`")
    for r in to_update:
        setup.update_ruleset(live_ids[r.name], built[r.name])
        changed.append(f"updated ruleset `{r.name}`")
    changed.append(f"backed up the previous settings and rulesets to {backup}")

    after = plan(config, setup)
    if after.drift:
        say("Still different after applying:")
        for line in render(after):
            say(line)
        return 1
    say("GitHub matches .repo-kit.toml.")
    return 0


def run_github(
    cwd: Path,
    *,
    apply_changes: bool,
    confirm: Callable[[str], bool] | None = None,
    changed: list[str] | None = None,
    say: Callable[[str], None] = print,
) -> int:
    """The command: this repo's config against its settings and rulesets on GitHub."""
    repo_root = git.toplevel(cwd)
    url = git.remote_url(repo_root)
    if not git.is_github_url(url):
        raise RkError(f"origin ({url}) isn't on GitHub: there are no settings or rulesets")
    owner, repo = git.parse_owner_repo(url)
    config = load_repo_config(repo_root)
    setup = GhCliGitHub(owner, repo)
    if not apply_changes:
        return run_diff(config, setup, say=say)
    from repo_kit.bootstrap import ask

    return apply(
        repo_root,
        config,
        setup,
        target=f"{owner}/{repo}",
        confirm=confirm or ask,
        changed=changed if changed is not None else [],
        say=say,
    )
