"""`rk status`'s pure parts: the counts, the text and the JSON document."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_kit import doctor, status
from repo_kit.doctor import Check, Status
from repo_kit.errors import RkError
from repo_kit.files import UNRELEASED
from repo_kit.github import PullRequest
from repo_kit.worktree import WorktreeStatus

PR = PullRequest(9, "closed", True, False, "dev", "fix/typo")
CHECKS = (
    Check(Status.OK, "tools", id="tools"),
    Check(Status.WARN, "1 stale worktree", "path\nfix it", "worktrees"),
    Check(Status.SKIP, "worktrees", id="skipped"),
)
STALE = WorktreeStatus(Path("/wt/fix-typo"), "fix/typo", PR, uncommitted=1, unpushed=2)
GONE = WorktreeStatus(Path("/wt/gone"), "feat/gone", None, uncommitted=None, unpushed=0)


def report(checks=CHECKS, worktrees=(STALE, GONE), note="", pin="1.4.0", engine="1.4.0"):
    return status.Report("o/r", pin, engine, tuple(worktrees), note, tuple(checks))


def test_counts_include_every_status_and_problems_are_warn_or_fail_only():
    rep = report([*CHECKS, Check(Status.FAIL, "bad", id="bad")])
    assert rep.counts == {"OK": 1, "WARN": 1, "FAIL": 1, "SKIP": 1}
    assert [c.id for c in rep.problems] == ["worktrees", "bad"]


def test_only_a_fail_makes_it_not_ok():
    assert report().ok
    assert not report([Check(Status.FAIL, "x", id="x")]).ok


@pytest.mark.parametrize(
    ("pin", "engine", "matches"),
    [("1.4.0", "1.4.0", True), ("1.4.0", "1.5.0", False), ("1.4.0", UNRELEASED, True)],
)
def test_pin_matches(pin, engine, matches):
    assert report(pin=pin, engine=engine).pin_matches is matches


def test_render_has_the_three_parts_and_names_problems_with_ids():
    text = status.render(report())
    assert text.splitlines()[0] == "o/r"
    assert "Pin: 1.4.0 (this rk 1.4.0)" in text
    assert "#9 merged" in text
    assert "directory missing" in text
    assert "Doctor: 3 checks: 1 OK, 1 WARN, 0 FAIL, 1 SKIP" in text
    assert "[WARN] 1 stale worktree (worktrees)" in text
    assert "(tools)" not in text  # OK checks are only counted


def test_render_says_when_the_pin_trails_and_when_there_are_no_worktrees():
    text = status.render(report(worktrees=(), engine="1.5.0"))
    assert "Pin: 1.4.0, but this rk is 1.5.0" in text
    assert "  none" in text


def test_json_is_the_same_data():
    doc = json.loads(status.to_json(report()))
    assert doc["schema_version"] == 1
    assert doc["ok"] is True
    assert doc["repo"] == "o/r"
    assert doc["pin"] == {"version": "1.4.0", "rk": "1.4.0", "matches": True}
    stale, gone = doc["worktrees"]
    assert stale["pr"] == {"number": 9, "state": "merged"}
    assert (stale["uncommitted"], stale["unpushed"]) == (1, 2)
    assert gone["pr"] is None
    assert gone["uncommitted"] is None
    assert doc["doctor"]["counts"] == {"OK": 1, "WARN": 1, "FAIL": 0, "SKIP": 1}
    assert doc["doctor"]["problems"] == [doctor.check_dict(CHECKS[1])]


def test_json_marks_unknown_prs():
    doc = json.loads(status.to_json(report(note="PRs unknown: origin is not on GitHub")))
    assert all(w["pr_known"] is False for w in doc["worktrees"])
    assert doc["worktrees_note"].startswith("PRs unknown")


def test_error_json():
    assert json.loads(status.error_json("no repo")) == {
        "schema_version": 1,
        "ok": False,
        "error": "no repo",
    }


def test_run_status_exit_codes_and_json_only(monkeypatch):
    monkeypatch.setattr(status.Context, "load", staticmethod(lambda cwd: object()))
    monkeypatch.setattr(status.doctor, "run_checks", lambda cwd: list(CHECKS))
    monkeypatch.setattr(status, "build", lambda ctx, checks: report(checks))
    said: list[str] = []
    assert status.run_status(Path(), as_json=True, say=said.append) == 0
    assert len(said) == 1
    assert json.loads(said[0])["ok"] is True

    failing = [Check(Status.FAIL, "x", id="x")]
    monkeypatch.setattr(status.doctor, "run_checks", lambda cwd: failing)
    assert status.run_status(Path(), say=said.append) == 1

    def boom(cwd):
        raise RkError("not a git repository")

    monkeypatch.setattr(status.Context, "load", staticmethod(boom))
    said.clear()
    assert status.run_status(Path(), as_json=True, say=said.append) == 1
    assert json.loads(said[0])["error"] == "not a git repository"
    with pytest.raises(RkError):
        status.run_status(Path(), say=said.append)
