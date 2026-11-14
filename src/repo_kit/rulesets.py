"""Build the `repo-kit: dev`, `repo-kit: main` and `repo-kit: tags` rulesets, and diff them.

Built as Python dicts from the profile and config (never templated JSON), compared with the live
rulesets read through `gh api`. `rk github diff` and `rk doctor` report the drift; applying it is
`rk github apply`, still to come. See docs/rulesets.md.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from repo_kit import git
from repo_kit.checks import REQUIRED_CHECKS
from repo_kit.config import RepoConfig, load_repo_config
from repo_kit.errors import RkError
from repo_kit.github import GhCliGitHub, RepoSetup

# GitHub's built-in Admin repository role, as a ruleset bypass actor.
ADMIN_ROLE = 5
# The GitHub Actions App: required checks only count when Actions reports them.
ACTIONS_APP_ID = 15368

PREFIX = "repo-kit: "

# The `pull_request` rule parameters rk sets. GitHub returns more; those aren't compared.
_PR_PARAMS: dict[str, Any] = {
    "required_approving_review_count": 0,
    "dismiss_stale_reviews_on_push": False,
    "require_code_owner_review": False,
    "require_last_push_approval": False,
    "required_review_thread_resolution": False,
    "allowed_merge_methods": ["merge"],
}
_CHECK_FLAGS = ("strict_required_status_checks_policy", "do_not_enforce_on_create")


def _actor(actor_type: str, actor_id: int, mode: str) -> dict[str, Any]:
    return {"actor_id": actor_id, "actor_type": actor_type, "bypass_mode": mode}


def _branch_ruleset(
    config: RepoConfig, branch: str, bypass: list[dict[str, Any]]
) -> dict[str, Any]:
    required = [*REQUIRED_CHECKS, *config.checks.required]
    return {
        "name": f"{PREFIX}{branch}",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": [f"refs/heads/{branch}"], "exclude": []}},
        "bypass_actors": bypass,
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "pull_request", "parameters": dict(_PR_PARAMS)},
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [
                        {"context": name, "integration_id": ACTIONS_APP_ID} for name in required
                    ],
                },
            },
        ],
    }


def build(config: RepoConfig) -> list[dict[str, Any]]:
    """The rulesets the config describes, as API payloads (`dev-main`: dev, main, tags)."""
    admin = _actor("RepositoryRole", ADMIN_ROLE, "pull_request")
    release_app = _actor("Integration", config.release.app_id, "always")
    dev_bypass = [admin]
    if config.release.back_merge:
        dev_bypass.append(release_app)  # release.yml pushes the back-merge straight to dev
    return [
        _branch_ruleset(config, config.default_base, dev_bypass),
        _branch_ruleset(config, config.release_branch, [admin]),
        {
            "name": f"{PREFIX}tags",
            "target": "tag",
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
            # Only the release App creates v* tags. Nobody, not even admin, moves or deletes one.
            "bypass_actors": [release_app],
            "rules": [{"type": "deletion"}, {"type": "update"}, {"type": "creation"}],
        },
    ]


# --- comparing -----------------------------------------------------------------------------


def _text(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _sorted_list(value: Any) -> str:
    return ", ".join(sorted(str(v) for v in value or [])) or "(none)"


def canonical(ruleset: dict[str, Any]) -> dict[str, str]:
    """`key -> value` lines for the fields rk sets, so a built and a live ruleset compare.

    List order never counts. GitHub's extra `pull_request` keys are ignored; a rule type rk
    doesn't set still shows up, as drift.
    """
    ref_name = (ruleset.get("conditions") or {}).get("ref_name") or {}
    lines = {
        "target": str(ruleset.get("target")),
        "enforcement": str(ruleset.get("enforcement")),
        "include": _sorted_list(ref_name.get("include")),
        "exclude": _sorted_list(ref_name.get("exclude")),
        "bypass": _sorted_list(
            f"{a.get('actor_type')} {a.get('actor_id')} {a.get('bypass_mode')}"
            for a in ruleset.get("bypass_actors") or []
        ),
    }
    for rule in ruleset.get("rules") or []:
        kind = rule.get("type")
        params = rule.get("parameters") or {}
        key = f"rule {kind}"
        if kind == "pull_request":
            for name in _PR_PARAMS:
                value = params.get(name)
                lines[f"{key}: {name}"] = (
                    _sorted_list(value) if isinstance(value, list) else _text(value)
                )
        elif kind == "required_status_checks":
            for name in _CHECK_FLAGS:
                lines[f"{key}: {name}"] = _text(params.get(name))
            lines[f"{key}: checks"] = _sorted_list(
                f"{c.get('context')} (app {c.get('integration_id', 'any')})"
                for c in params.get("required_status_checks") or []
            )
        else:
            lines[key] = _text(params) if params else "on"
    return lines


class State(StrEnum):
    MATCHES = "matches"
    DRIFTED = "drifted"
    MISSING = "missing"


@dataclass(frozen=True)
class Change:
    key: str
    live: str | None  # None: not set live
    wanted: str | None  # None: rk doesn't want it


@dataclass(frozen=True)
class RulesetDiff:
    name: str
    state: State
    changes: tuple[Change, ...] = ()


@dataclass(frozen=True)
class RulesetsDiff:
    rulesets: tuple[RulesetDiff, ...]
    others: tuple[str, ...] = field(default=())  # live rulesets rk doesn't own: left alone

    @property
    def drift(self) -> bool:
        return any(r.state is not State.MATCHES for r in self.rulesets)


def diff(config: RepoConfig, live: list[dict[str, Any]]) -> RulesetsDiff:
    """Compare what the config describes with the live rulesets (full detail, from the API)."""
    by_name = {r.get("name"): r for r in live}
    wanted = build(config)
    ours = {r["name"] for r in wanted}
    results = []
    for ruleset in wanted:
        current = by_name.get(ruleset["name"])
        if current is None:
            results.append(RulesetDiff(ruleset["name"], State.MISSING))
            continue
        want, have = canonical(ruleset), canonical(current)
        changes = tuple(
            Change(key, have.get(key), want.get(key))
            for key in [*want, *(k for k in have if k not in want)]
            if have.get(key) != want.get(key)
        )
        state = State.DRIFTED if changes else State.MATCHES
        results.append(RulesetDiff(ruleset["name"], state, changes))
    others = tuple(sorted(str(n) for n in by_name if n not in ours))
    return RulesetsDiff(tuple(results), others)


def render(result: RulesetsDiff) -> list[str]:
    lines = []
    for r in result.rulesets:
        if r.state is State.MISSING:
            lines.append(f"{r.name}: missing (would be created)")
            continue
        lines.append(f"{r.name}: {r.state}")
        for c in r.changes:
            lines.append(f"  {c.key}:")
            if c.live is not None:
                lines.append(f"    - {c.live}")
            if c.wanted is not None:
                lines.append(f"    + {c.wanted}")
    if result.others:
        lines.append(f"Left alone (not rk's): {', '.join(result.others)}")
    return lines


def run_diff(config: RepoConfig, setup: RepoSetup, *, say: Callable[[str], None] = print) -> int:
    """`rk github diff`: live rulesets against the config. Read-only; 1 if anything differs."""
    result = diff(config, setup.rulesets())
    for line in render(result):
        say(line)
    if result.drift:
        say(
            "Rulesets differ from .repo-kit.toml (- live, + wanted). "
            "`rk github apply` (not built yet) will apply them."
        )
        return 1
    say("Rulesets match .repo-kit.toml.")
    return 0


def run_github_diff(cwd: Path, *, say: Callable[[str], None] = print) -> int:
    """The command: this repo's config against its rulesets on GitHub."""
    repo_root = git.toplevel(cwd)
    url = git.remote_url(repo_root)
    if not git.is_github_url(url):
        raise RkError(f"origin ({url}) isn't on GitHub: there are no rulesets to compare")
    owner, repo = git.parse_owner_repo(url)
    return run_diff(load_repo_config(repo_root), GhCliGitHub(owner, repo), say=say)
