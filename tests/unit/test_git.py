import pytest

from repo_kit.git import MIN_GIT, GitError, parse_owner_repo, parse_version
from repo_kit.github import PullRequest, pick_pr, pr_from_api


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/GreenMachine582/repo-kit.git", ("GreenMachine582", "repo-kit")),
        ("https://github.com/GreenMachine582/repo-kit", ("GreenMachine582", "repo-kit")),
        ("git@github.com:GreenMachine582/photo-ledger.git", ("GreenMachine582", "photo-ledger")),
        ("ssh://git@github.com/o/r.git", ("o", "r")),
        ("/tmp/x/origin.git", ("local", "origin")),
        ("C:\\Users\\me\\origin.git", ("local", "origin")),
    ],
)
def test_parse_owner_repo(url, expected):
    assert parse_owner_repo(url) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("git version 2.47.1", (2, 47)),
        ("git version 2.54.0.windows.1", (2, 54)),
        ("git version 2.39.5 (Apple Git-154)", (2, 39)),
    ],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def test_parse_version_garbage():
    with pytest.raises(GitError):
        parse_version("nope")


def test_min_git():
    assert MIN_GIT == (2, 38)


def _pr(number, state="open", merged=False):
    return PullRequest(number, state, merged, False, "dev", "feat/x")


def test_pick_pr_prefers_open_then_latest():
    assert pick_pr([]) is None
    assert pick_pr([_pr(1, "closed"), _pr(2), _pr(3, "closed")]).number == 2
    assert pick_pr([_pr(1, "closed"), _pr(4, "closed", merged=True)]).number == 4


def test_pr_from_api():
    pr = pr_from_api(
        {
            "number": 7,
            "state": "closed",
            "merged_at": "2026-11-01T00:00:00Z",
            "draft": False,
            "base": {"ref": "dev", "repo": {"full_name": "o/r"}},
            "head": {"ref": "feat/x", "sha": "abc123", "repo": {"full_name": "o/r"}},
            "labels": [{"name": "needs-merge"}],
        }
    )
    assert pr == PullRequest(
        7, "closed", True, False, "dev", "feat/x", ("needs-merge",), head_sha="abc123"
    )
    assert pr.finished
    assert not pr.from_fork


@pytest.mark.parametrize("head_repo", [{"full_name": "someone/r"}, None])
def test_pr_from_api_marks_forks_and_deleted_forks(head_repo):
    pr = pr_from_api(
        {
            "number": 8,
            "state": "open",
            "base": {"ref": "dev", "repo": {"full_name": "o/r"}},
            "head": {"ref": "feat/x", "sha": "abc123", "repo": head_repo},
        }
    )
    assert pr.from_fork
