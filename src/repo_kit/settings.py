"""Build the repo settings the profile describes, and diff them against GitHub's.

Replaces each repo's `.github/repo-settings.json`. `rk github diff` and `rk doctor` report the
drift, and `rk github apply` patches it. See docs/rulesets.md.
"""

from __future__ import annotations

import json
from typing import Any

from repo_kit.config import RepoConfig
from repo_kit.rulesets import Change, RulesetDiff, State

NAME = "repo settings"
# What GitHub leaves out of `GET /repos/{o}/{r}` for someone who isn't an admin.
NOT_VISIBLE = "(not visible: needs admin)"


def build(config: RepoConfig) -> dict[str, Any]:
    """The settings `PATCH /repos/{o}/{r}` takes, for the `dev-main` profile."""
    return {
        "default_branch": config.default_base,
        # Merge commits only: history is never rewritten, and release-please reads every commit.
        "allow_merge_commit": True,
        "allow_squash_merge": False,
        "allow_rebase_merge": False,
        "delete_branch_on_merge": True,
        "allow_update_branch": True,
        "allow_auto_merge": True,
        # The merge commit is titled "Merge pull request #N from …", with the PR title as its body.
        "merge_commit_title": "MERGE_MESSAGE",
        "merge_commit_message": "PR_TITLE",
    }


def diff(config: RepoConfig, live: dict[str, Any]) -> RulesetDiff:
    """The settings rk sets that differ live. Every other setting is left out of the comparison."""
    changes = []
    for key, wanted in build(config).items():
        have = json.dumps(live[key]) if key in live else NOT_VISIBLE
        want = json.dumps(wanted)
        if have != want:
            changes.append(Change(key, have, want))
    return RulesetDiff(NAME, State.DRIFTED if changes else State.MATCHES, tuple(changes))


def changed_values(config: RepoConfig, result: RulesetDiff) -> dict[str, Any]:
    """The values to patch: only the settings that differ."""
    wanted = build(config)
    return {c.key: wanted[c.key] for c in result.changes}
