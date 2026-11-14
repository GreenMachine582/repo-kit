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
