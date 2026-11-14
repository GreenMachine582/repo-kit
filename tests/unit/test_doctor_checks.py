"""`rk doctor`'s pure parts: the gh version, the tools line, the report format, the exit rule."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_kit import doctor, git
from repo_kit.doctor import Check, Status
from repo_kit.errors import RkError


@pytest.mark.parametrize(
    ("text", "version"),
    [
        (
            "gh version 2.63.0 (2024-11-27)\nhttps://github.com/cli/cli/releases/tag/v2.63.0\n",
            (2, 63),
        ),
        ("gh version 2.40.1 (2023-12-13)", (2, 40)),
    ],
)
def test_parse_gh_version(text, version):
    assert doctor.parse_gh_version(text) == version


def test_parse_gh_version_refuses_other_text():
    with pytest.raises(RkError):
        doctor.parse_gh_version("hub version 2.14")


@pytest.fixture
def git_247(monkeypatch):
    monkeypatch.setattr(git, "version", lambda _cwd: (2, 47))


def test_tools_ok(git_247):
    check = doctor.check_tools(Path(), lambda: (2, 63))
    assert check.status is Status.OK
    assert check.label.startswith("git 2.47 / gh 2.63 / Python 3.")


def test_tools_missing_gh_warns(git_247):
    check = doctor.check_tools(Path(), lambda: None)
    assert check.status is Status.WARN
    assert "gh missing" in check.label
    assert "gh auth login" in check.detail


def test_tools_old_gh_fails(git_247):
    check = doctor.check_tools(Path(), lambda: (2, 39))
    assert check.status is Status.FAIL
    assert "gh 2.40 or newer" in check.detail


def test_tools_old_git_fails(monkeypatch):
    monkeypatch.setattr(git, "version", lambda _cwd: (2, 37))
    check = doctor.check_tools(Path(), lambda: (2, 63))
    assert check.status is Status.FAIL
    assert "Git 2.38 or newer" in check.detail


def test_render_aligns_status_and_indents_detail():
    text = doctor.render(
        [
            Check(Status.OK, "managed files: 9 in sync, 0 drifted"),
            Check(Status.WARN, "1 stale worktree", "a (feat/x: PR #1 merged)\nrk wt done"),
            Check(Status.FAIL, "gth-pr-bot not enrolled"),
            Check(Status.SKIP, "repo settings"),
        ]
    )
    assert text.splitlines() == [
        "[OK]   managed files: 9 in sync, 0 drifted",
        "[WARN] 1 stale worktree",
        "       a (feat/x: PR #1 merged)",
        "       rk wt done",
        "[FAIL] gth-pr-bot not enrolled",
        "[SKIP] repo settings",
    ]


def test_only_a_fail_fails():
    assert not doctor.failed(
        [Check(Status.OK, "a"), Check(Status.WARN, "b"), Check(Status.SKIP, "c")]
    )
    assert doctor.failed([Check(Status.OK, "a"), Check(Status.FAIL, "b")])
