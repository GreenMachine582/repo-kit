"""`GhCliGitHub` with `gh` replaced: the requests it makes and how it reads the answers."""

from __future__ import annotations

import base64
import json
import subprocess

import pytest

from repo_kit import github as github_module
from repo_kit.github import BaseStatus, GhCliGitHub, GitHubError, PullRequest


class FakeGh:
    """Stands in for `subprocess.run`: answers in order and records each `gh api` argument list."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *answers: object) -> None:
        self.answers = list(answers)
        self.calls: list[list[str]] = []
        self.stdin: list[object] = []
        monkeypatch.setattr(github_module.subprocess, "run", self)

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv[0] == "gh"
        # `gh api` calls are recorded without the prefix; other gh commands keep theirs.
        self.calls.append(argv[2:] if argv[1] == "api" else argv[1:])
        self.stdin.append(kwargs.get("input"))
        answer = self.answers.pop(0)
        if isinstance(answer, subprocess.CompletedProcess):
            return answer
        return subprocess.CompletedProcess(argv, 0, json.dumps(answer), "")


def failure(stderr: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 1, "", stderr)


def api_pr(number: int) -> dict[str, object]:
    repo = {"full_name": "o/r"}
    return {
        "number": number,
        "state": "open",
        "base": {"ref": "dev", "repo": repo},
        "head": {"ref": f"feat/{number}", "sha": f"sha{number}", "repo": repo},
    }


PR = PullRequest(5, "open", False, False, "dev", "feat/a-b", head_sha="abc")


@pytest.fixture
def client() -> GhCliGitHub:
    return GhCliGitHub("o", "r")


def test_list_open_prs_reads_every_page(client, monkeypatch):
    gh = FakeGh(monkeypatch, [api_pr(n) for n in range(100)], [api_pr(100)])
    prs = client.list_open_prs()
    assert [pr.number for pr in prs] == list(range(101))
    assert prs[100].head_sha == "sha100"
    assert [call[0].rsplit("page=", 1)[1] for call in gh.calls] == ["1", "2"]


def test_list_open_prs_stops_on_a_short_page(client, monkeypatch):
    gh = FakeGh(monkeypatch, [api_pr(1)])
    assert len(client.list_open_prs()) == 1
    assert len(gh.calls) == 1


def test_base_status_current_needs_only_the_compare(client, monkeypatch):
    gh = FakeGh(monkeypatch, {"behind_by": 0})
    assert client.base_status(PR) is BaseStatus.CURRENT
    assert gh.calls == [["repos/o/r/compare/dev...feat/a-b?per_page=1"]]


@pytest.mark.parametrize(
    ("mergeable", "expected"),
    [
        (True, BaseStatus.BEHIND),
        (False, BaseStatus.CONFLICTING),
        (None, BaseStatus.UNKNOWN),
    ],
)
def test_base_status_when_behind(client, monkeypatch, mergeable, expected):
    gh = FakeGh(monkeypatch, {"behind_by": 2}, {"mergeable": mergeable})
    assert client.base_status(PR) is expected
    assert gh.calls[1] == ["repos/o/r/pulls/5"]


def test_update_branch_accepted_sends_the_expected_head(client, monkeypatch):
    gh = FakeGh(monkeypatch, {"message": "Updating pull request branch."})
    assert client.update_branch(5, "abc").ok
    assert gh.calls == [
        ["-X", "PUT", "repos/o/r/pulls/5/update-branch", "-f", "expected_head_sha=abc"]
    ]


def test_update_branch_422_is_a_result(client, monkeypatch):
    FakeGh(monkeypatch, failure("gh: refused (HTTP 422)"))
    result = client.update_branch(5, "abc")
    assert not result.ok
    assert "HTTP 422" in result.detail


def test_update_branch_other_failures_raise(client, monkeypatch):
    FakeGh(monkeypatch, failure("gh: Resource not accessible by integration (HTTP 403)"))
    with pytest.raises(GitHubError, match="HTTP 403"):
        client.update_branch(5, "abc")


def test_add_label(client, monkeypatch):
    gh = FakeGh(monkeypatch, [])
    client.add_label(5, "needs-merge")
    assert gh.calls == [["-X", "POST", "repos/o/r/issues/5/labels", "-f", "labels[]=needs-merge"]]


def test_upsert_comment_creates_when_the_marker_is_missing(client, monkeypatch):
    gh = FakeGh(monkeypatch, [{"id": 1, "body": "someone else"}], {"id": 2})
    client.upsert_comment(5, "<!-- m -->", "conflict")
    assert gh.calls[1] == [
        "-X",
        "POST",
        "repos/o/r/issues/5/comments",
        "-f",
        "body=<!-- m -->\nconflict",
    ]


def test_upsert_comment_edits_the_marked_comment(client, monkeypatch):
    gh = FakeGh(monkeypatch, [{"id": 9, "body": "<!-- m -->\nold"}], {"id": 9})
    client.upsert_comment(5, "<!-- m -->", "new")
    assert gh.calls[1] == [
        "-X",
        "PATCH",
        "repos/o/r/issues/comments/9",
        "-f",
        "body=<!-- m -->\nnew",
    ]


def test_read_file_decodes_the_contents_api(client, monkeypatch):
    encoded = base64.b64encode(b'version = "1.0.0"\n').decode() + "\n"
    gh = FakeGh(monkeypatch, {"content": encoded})
    assert client.read_file(".repo-kit.toml") == 'version = "1.0.0"\n'
    assert gh.calls == [["repos/o/r/contents/.repo-kit.toml"]]


def test_read_file_missing_is_none(client, monkeypatch):
    FakeGh(monkeypatch, failure("gh: Not Found (HTTP 404)"))
    assert client.read_file(".repo-kit.toml") is None


def test_upsert_comment_leaves_an_unchanged_comment_alone(client, monkeypatch):
    gh = FakeGh(monkeypatch, [{"id": 9, "body": "<!-- m -->\nsame"}])
    client.upsert_comment(5, "<!-- m -->", "same")
    assert len(gh.calls) == 1


def test_set_secret_sends_the_value_on_stdin_only(client, monkeypatch):
    gh = FakeGh(monkeypatch, subprocess.CompletedProcess([], 0, "", ""))
    client.set_secret("GTH_PR_BOT_PRIVATE_KEY", "-----BEGIN KEY-----")
    assert gh.calls == [["secret", "set", "GTH_PR_BOT_PRIVATE_KEY", "--repo", "o/r"]]
    assert gh.stdin == ["-----BEGIN KEY-----"]
    assert not any("BEGIN" in arg for arg in gh.calls[0])


def test_a_failed_secret_set_never_shows_the_value(client, monkeypatch):
    FakeGh(monkeypatch, failure("gh: HTTP 403"))
    with pytest.raises(GitHubError) as exc:
        client.set_secret("S", "the-secret-value")
    assert "the-secret-value" not in str(exc.value)


def test_set_environment_secret_sends_the_value_on_stdin_only(client, monkeypatch):
    gh = FakeGh(monkeypatch, subprocess.CompletedProcess([], 0, "", ""))
    client.set_environment_secret("gth-pr-bot", "GTH_PR_BOT_PRIVATE_KEY", "-----BEGIN KEY-----")
    assert gh.calls == [
        ["secret", "set", "GTH_PR_BOT_PRIVATE_KEY", "--env", "gth-pr-bot", "--repo", "o/r"]
    ]
    assert gh.stdin == ["-----BEGIN KEY-----"]


def test_environment_secret_names_and_delete_secret(client, monkeypatch):
    done = subprocess.CompletedProcess([], 0, "", "")
    gh = FakeGh(monkeypatch, [{"name": "S"}], done)
    assert client.environment_secret_names("gth-pr-bot") == ["S"]
    client.delete_secret("S")
    assert gh.calls == [
        ["secret", "list", "--env", "gth-pr-bot", "--repo", "o/r", "--json", "name"],
        ["secret", "delete", "S", "--repo", "o/r"],
    ]


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        ([failure("gh: Not Found (HTTP 404)")], None),
        ([{"deployment_branch_policy": None}], ["*"]),
        ([{"deployment_branch_policy": {"protected_branches": True}}], ["<protected>"]),
        (
            [
                {"deployment_branch_policy": {"custom_branch_policies": True}},
                {
                    "branch_policies": [
                        {"id": 1, "name": "dev"},
                        {"id": 2, "name": "v*", "type": "tag"},
                    ]
                },
            ],
            ["dev", "tag:v*"],
        ),
    ],
)
def test_environment_branches(client, monkeypatch, answers, expected):
    gh = FakeGh(monkeypatch, *answers)
    assert client.environment_branches("gth-pr-bot") == expected
    assert gh.calls[0] == ["repos/o/r/environments/gth-pr-bot"]


def test_ensure_environment_keeps_only_the_one_branch(client, monkeypatch):
    policies = {
        "branch_policies": [{"id": 1, "name": "dev", "type": "branch"}, {"id": 2, "name": "*"}]
    }
    gh = FakeGh(monkeypatch, {}, policies, None)
    client.ensure_environment("gth-pr-bot", "dev")
    env = "repos/o/r/environments/gth-pr-bot"
    assert gh.calls == [
        ["-X", "PUT", env, "--input", "-"],
        [f"{env}/deployment-branch-policies"],
        ["-X", "DELETE", f"{env}/deployment-branch-policies/2"],
    ]
    assert json.loads(str(gh.stdin[0])) == {
        "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}
    }


def test_ensure_environment_adds_a_missing_branch(client, monkeypatch):
    gh = FakeGh(monkeypatch, {}, {"branch_policies": []}, {"id": 3})
    client.ensure_environment("gth-release-bot", "main")
    path = "repos/o/r/environments/gth-release-bot/deployment-branch-policies"
    assert gh.calls[-1] == ["-X", "POST", path, "--input", "-"]
    assert json.loads(str(gh.stdin[-1])) == {"name": "main", "type": "branch"}


def test_variables_and_secret_names(client, monkeypatch):
    gh = FakeGh(monkeypatch, [{"name": "V", "value": "1"}], [{"name": "S"}])
    assert client.variables() == {"V": "1"}
    assert client.secret_names() == ["S"]
    assert gh.calls == [
        ["variable", "list", "--repo", "o/r", "--json", "name,value"],
        ["secret", "list", "--repo", "o/r", "--json", "name"],
    ]


def test_set_variable_and_create_label(client, monkeypatch):
    done = subprocess.CompletedProcess([], 0, "", "")
    gh = FakeGh(monkeypatch, done, {"name": "wip"})
    client.set_variable("V", "1")
    client.create_label("wip", "fbca04", "under rework")
    assert gh.calls == [
        ["variable", "set", "V", "--repo", "o/r", "--body", "1"],
        [
            "-X",
            "POST",
            "repos/o/r/labels",
            "-f",
            "name=wip",
            "-f",
            "color=fbca04",
            "-f",
            "description=under rework",
        ],
    ]


def test_retarget_patches_the_base(client, monkeypatch):
    gh = FakeGh(monkeypatch, {})
    client.retarget(7, "dev")
    assert gh.calls == [["-X", "PATCH", "repos/o/r/pulls/7", "-f", "base=dev"]]


def ok() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, "", "")


def test_repo_settings_add_immutable_releases(client, monkeypatch):
    gh = FakeGh(monkeypatch, {"allow_merge_commit": True}, {"enabled": False})
    settings = client.repo_settings()
    assert settings == {"allow_merge_commit": True, "immutable_releases": False}
    assert gh.calls == [["repos/o/r"], ["repos/o/r/immutable-releases"]]


def test_immutable_releases_are_left_out_when_not_visible(client, monkeypatch):
    FakeGh(
        monkeypatch, {"allow_merge_commit": True}, failure("gh: Must have admin rights (HTTP 403)")
    )
    assert client.repo_settings() == {"allow_merge_commit": True}


def test_immutable_releases_have_their_own_endpoint(client, monkeypatch):
    gh = FakeGh(monkeypatch, ok(), ok())
    client.update_repo_settings({"immutable_releases": True, "allow_squash_merge": False})
    assert gh.calls[0] == ["-X", "PUT", "repos/o/r/immutable-releases"]
    assert gh.calls[1] == ["-X", "PATCH", "repos/o/r", "--input", "-"]
    assert json.loads(gh.stdin[1]) == {"allow_squash_merge": False}


def test_only_immutable_releases_sends_no_patch(client, monkeypatch):
    gh = FakeGh(monkeypatch, ok())
    client.update_repo_settings({"immutable_releases": False})
    assert gh.calls == [["-X", "DELETE", "repos/o/r/immutable-releases"]]


def test_pr_reads_one_pr_by_number(client, monkeypatch):
    gh = FakeGh(monkeypatch, api_pr(7))
    pr = client.pr(7)
    assert gh.calls == [["repos/o/r/pulls/7"]]
    assert pr is not None
    assert (pr.number, pr.head_ref, pr.head_sha) == (7, "feat/7", "sha7")


def test_pr_that_is_not_there_is_none(client, monkeypatch):
    FakeGh(monkeypatch, failure("gh: Not Found (HTTP 404)"))
    assert client.pr(7) is None


def test_create_pr_posts_the_branch_and_reads_the_pr_back(client, monkeypatch):
    gh = FakeGh(monkeypatch, api_pr(9))
    pr = client.create_pr("chore/upgrade-1.1.0", "dev", "Move repo-kit to 1.1.0", "body text")
    assert gh.calls == [
        [
            "-X",
            "POST",
            "repos/o/r/pulls",
            "-f",
            "head=chore/upgrade-1.1.0",
            "-f",
            "base=dev",
            "-f",
            "title=Move repo-kit to 1.1.0",
            "-f",
            "body=body text",
            "-F",
            "draft=false",
        ]
    ]
    assert pr.number == 9


def test_create_pr_can_open_a_draft(client, monkeypatch):
    gh = FakeGh(monkeypatch, api_pr(9))
    client.create_pr("a", "dev", "t", "b", draft=True)
    assert gh.calls[0][-2:] == ["-F", "draft=true"]
