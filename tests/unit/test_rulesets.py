"""The dev-main rulesets built from config, and how they're compared with GitHub's."""

from __future__ import annotations

import copy

import pytest

from repo_kit.config import ChecksConfig, ReleaseConfig, RepoConfig
from repo_kit.rulesets import State, build, canonical, diff, render, run_diff

ADMIN = {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}
RELEASE_APP = {"actor_id": 5058502, "actor_type": "Integration", "bypass_mode": "always"}
PR_RULE = {
    "type": "pull_request",
    "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": False,
        "require_code_owner_review": False,
        "require_last_push_approval": False,
        "required_review_thread_resolution": False,
        "allowed_merge_methods": ["merge"],
    },
}


def checks_rule(*contexts: str) -> dict:
    return {
        "type": "required_status_checks",
        "parameters": {
            "strict_required_status_checks_policy": False,
            "do_not_enforce_on_create": False,
            "required_status_checks": [{"context": c, "integration_id": 15368} for c in contexts],
        },
    }


PR_CHECKS = ("pr-checks / commits", "pr-checks / pr-title", "pr-checks / managed-files")


def branch(name: str, bypass: list[dict]) -> dict:
    return {
        "name": f"repo-kit: {name}",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": [f"refs/heads/{name}"], "exclude": []}},
        "bypass_actors": bypass,
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            PR_RULE,
            checks_rule(*PR_CHECKS),
        ],
    }


def test_the_default_rulesets_snapshot():
    assert build(RepoConfig()) == [
        branch("dev", [ADMIN, RELEASE_APP]),
        branch("main", [ADMIN]),
        {
            "name": "repo-kit: tags",
            "target": "tag",
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
            "bypass_actors": [RELEASE_APP],
            "rules": [{"type": "deletion"}, {"type": "update"}, {"type": "creation"}],
        },
    ]


def test_the_repos_own_checks_follow_the_pr_checks():
    dev = build(RepoConfig(checks=ChecksConfig(required=("test",))))[0]
    assert dev["rules"][3] == checks_rule(*PR_CHECKS, "test")


def test_without_back_merge_the_app_doesnt_bypass_dev():
    dev, _main, tags = build(RepoConfig(release=ReleaseConfig(back_merge=False)))
    assert dev["bypass_actors"] == [ADMIN]
    assert tags["bypass_actors"] == [RELEASE_APP]


def test_the_app_id_comes_from_config():
    dev, _main, tags = build(RepoConfig(release=ReleaseConfig(app_id=42)))
    assert dev["bypass_actors"][1]["actor_id"] == 42
    assert tags["bypass_actors"][0]["actor_id"] == 42


def as_github_returns(ruleset: dict, ruleset_id: int) -> dict:
    """What `GET …/rulesets/{id}` returns: extra fields, extra defaults, any order."""
    live = copy.deepcopy(ruleset)
    live.update(
        id=ruleset_id,
        source="GreenMachine582/demo",
        source_type="Repository",
        _links={"self": {"href": "https://api.github.com/…"}},
        current_user_can_bypass="pull_requests_only",
    )
    live["bypass_actors"].reverse()
    live["rules"].reverse()
    for rule in live["rules"]:
        if rule["type"] == "pull_request":
            rule["parameters"]["require_extra_approval_for_unattributed_changes"] = True
            rule["parameters"]["required_reviewers"] = []
        if rule["type"] == "required_status_checks":
            rule["parameters"]["required_status_checks"].reverse()
    return live


def live_rulesets(config: RepoConfig | None = None) -> list[dict]:
    return [as_github_returns(r, i) for i, r in enumerate(build(config or RepoConfig()), 1)]


def test_github_extras_and_order_are_not_drift():
    result = diff(RepoConfig(), live_rulesets())
    assert not result.drift
    assert [r.state for r in result.rulesets] == [State.MATCHES] * 3


def edit_dev(change) -> list[dict]:
    live = live_rulesets()
    change(live[0])
    return live


def rule(ruleset: dict, kind: str) -> dict:
    return next(r for r in ruleset["rules"] if r["type"] == kind)


@pytest.mark.parametrize(
    ("change", "key"),
    [
        (
            lambda r: rule(r, "required_status_checks")["parameters"][
                "required_status_checks"
            ].pop(),
            "rule required_status_checks: checks",
        ),
        (
            lambda r: rule(r, "required_status_checks")["parameters"]["required_status_checks"][
                0
            ].pop("integration_id"),
            "rule required_status_checks: checks",
        ),
        (lambda r: r["bypass_actors"].append(dict(ADMIN, actor_id=2)), "bypass"),
        (
            lambda r: r["rules"].append({"type": "required_linear_history"}),
            "rule required_linear_history",
        ),
        (lambda r: r["conditions"]["ref_name"]["include"].append("refs/heads/x"), "include"),
        (
            lambda r: rule(r, "pull_request")["parameters"].update(
                allowed_merge_methods=["squash"]
            ),
            "rule pull_request: allowed_merge_methods",
        ),
        (lambda r: r.update(enforcement="evaluate"), "enforcement"),
        (lambda r: r["rules"].remove({"type": "deletion"}), "rule deletion"),
    ],
)
def test_each_kind_of_drift_is_caught(change, key):
    result = diff(RepoConfig(), edit_dev(change))
    dev = result.rulesets[0]
    assert dev.state is State.DRIFTED
    assert [c.key for c in dev.changes] == [key]


def test_an_unpinned_check_reads_as_any_app():
    live = edit_dev(
        lambda r: rule(r, "required_status_checks")["parameters"]["required_status_checks"][0].pop(
            "integration_id"
        )
    )
    [change] = diff(RepoConfig(), live).rulesets[0].changes
    assert "(app any)" in str(change.live)
    assert "(app any)" not in str(change.wanted)


def test_a_missing_ruleset_and_others_left_alone():
    live = live_rulesets()
    del live[2]
    live.append({"id": 9, "name": "release tags", "target": "tag", "rules": []})
    result = diff(RepoConfig(), live)
    assert result.drift
    assert result.rulesets[2].state is State.MISSING
    assert result.others == ("release tags",)


def test_rulesets_rk_doesnt_own_arent_drift():
    live = [*live_rulesets(), {"id": 9, "name": "dev", "target": "branch", "rules": []}]
    result = diff(RepoConfig(), live)
    assert not result.drift
    assert result.others == ("dev",)


def test_canonical_reads_a_bare_ruleset():
    lines = canonical({"name": "x"})
    assert lines["bypass"] == "(none)"
    assert lines["include"] == "(none)"


def test_render_shows_live_and_wanted():
    live = live_rulesets()
    live[0]["bypass_actors"] = [ADMIN]
    del live[1]
    lines = render(diff(RepoConfig(), live))
    assert lines == [
        "repo-kit: dev: drifted",
        "  bypass:",
        "    - RepositoryRole 5 pull_request",
        "    + Integration 5058502 always, RepositoryRole 5 pull_request",
        "repo-kit: main: missing (would be created)",
        "repo-kit: tags: matches",
    ]


class Setup:
    def __init__(self, live: list[dict]) -> None:
        self.live = live

    def rulesets(self) -> list[dict]:
        return self.live


def test_run_diff_exits_1_only_on_drift():
    said: list[str] = []
    assert run_diff(RepoConfig(), Setup(live_rulesets()), say=said.append) == 0  # type: ignore[arg-type]
    assert said[-1] == "Rulesets match .repo-kit.toml."

    said.clear()
    assert run_diff(RepoConfig(), Setup([]), say=said.append) == 1  # type: ignore[arg-type]
    assert said[0] == "repo-kit: dev: missing (would be created)"
    assert "(- live, + wanted)" in said[-1]
