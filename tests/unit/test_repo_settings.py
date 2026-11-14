"""The repo settings built from the profile, and how they're compared with GitHub's."""

from __future__ import annotations

from repo_kit.config import RepoConfig
from repo_kit.rulesets import State
from repo_kit.settings import NOT_VISIBLE, build, changed_values, diff


def test_the_dev_main_settings_snapshot():
    assert build(RepoConfig()) == {
        "default_branch": "dev",
        "allow_merge_commit": True,
        "allow_squash_merge": False,
        "allow_rebase_merge": False,
        "delete_branch_on_merge": True,
        "allow_update_branch": True,
        "allow_auto_merge": True,
        "merge_commit_title": "MERGE_MESSAGE",
        "merge_commit_message": "PR_TITLE",
    }


def live(**changes) -> dict:
    """What `GET /repos/{o}/{r}` returns: the settings rk sets, among many others."""
    return {**build(RepoConfig()), "has_wiki": True, "full_name": "o/r", **changes}


def test_matching_settings_and_github_extras_are_not_drift():
    result = diff(RepoConfig(), live())
    assert (result.name, result.state, result.changes) == ("repo settings", State.MATCHES, ())


def test_a_changed_setting_is_drift_with_json_values():
    result = diff(RepoConfig(), live(allow_squash_merge=True, default_branch="main"))
    assert result.state is State.DRIFTED
    assert [(c.key, c.live, c.wanted) for c in result.changes] == [
        ("default_branch", '"main"', '"dev"'),
        ("allow_squash_merge", "true", "false"),
    ]
    assert changed_values(RepoConfig(), result) == {
        "default_branch": "dev",
        "allow_squash_merge": False,
    }


def test_a_setting_only_an_admin_sees_is_drift():
    settings = live()
    del settings["allow_update_branch"]
    [change] = diff(RepoConfig(), settings).changes
    assert (change.key, change.live) == ("allow_update_branch", NOT_VISIBLE)
