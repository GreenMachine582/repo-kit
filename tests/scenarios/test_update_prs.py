"""`rk update-prs` scenarios 1-7, 9 and 10 (docs/testing.md), against a bare origin."""

from __future__ import annotations

import pytest
from harness import advance, contains, git, push_branch, remote_sha

from repo_kit.config import RepoConfig
from repo_kit.github import GitHubError
from repo_kit.reconcile import COMMENT_MARKER, Action, reconcile


@pytest.fixture
def work(clone):
    return clone("work")


def run(github):
    return reconcile(github, RepoConfig(), sleep=lambda _seconds: None)


def actions(report):
    return {row.head: row.action for row in report.rows}


def has_base(work, sha, branch):
    git(work, "fetch", "-q", "origin")
    return contains(work, sha, f"origin/{branch}")


def test_scenario_1_clean_base_merge(work, github):
    head = push_branch(work, "feat/a")
    base = advance(work)
    github.add_pr("feat/a")

    report = run(github)

    assert actions(report) == {"feat/a": Action.UPDATED}
    git(work, "fetch", "-q", "origin")
    parents = git(work, "log", "-1", "--format=%P", "origin/feat/a").split()
    assert parents == [head, base]
    assert len(report.changed) == 1


def test_scenario_2_conflict_gets_a_label_and_one_comment(work, github):
    head = push_branch(work, "feat/a", filename="same.txt", content="ours\n")
    advance(work, filename="same.txt", content="theirs\n")
    pr = github.add_pr("feat/a")

    report = run(github)

    assert actions(report) == {"feat/a": Action.CONFLICT}
    assert remote_sha(work, "feat/a") == head
    assert github.get(pr.number).labels == ("needs-merge",)
    [comment] = github.comments[pr.number]
    assert COMMENT_MARKER in comment
    assert "rk wt sync feat/a" in comment


def test_scenario_3_a_rerun_changes_nothing(work, github):
    push_branch(work, "feat/a")
    push_branch(work, "feat/b", filename="same.txt", content="ours\n")
    advance(work, filename="same.txt", content="theirs\n")
    updated = github.add_pr("feat/a")
    conflicting = github.add_pr("feat/b")
    run(github)
    sha = remote_sha(work, "feat/a")

    report = run(github)

    assert actions(report) == {"feat/a": Action.UP_TO_DATE, "feat/b": Action.CONFLICT}
    assert remote_sha(work, "feat/a") == sha
    assert github.get(updated.number).labels == ()
    assert github.added_labels == [(conflicting.number, "needs-merge")]
    assert len(github.comments[conflicting.number]) == 1


def test_scenario_4_two_prs_sharing_a_base_both_update(work, github):
    push_branch(work, "feat/a")
    push_branch(work, "feat/b")
    base = advance(work)
    github.add_pr("feat/a")
    github.add_pr("feat/b")

    report = run(github)

    assert set(actions(report).values()) == {Action.UPDATED}
    assert has_base(work, base, "feat/a")
    assert has_base(work, base, "feat/b")


def test_scenario_5_chain_of_three_updates_parents_first(work, github):
    push_branch(work, "feat/a")
    push_branch(work, "feat/b", start="origin/feat/a")
    push_branch(work, "feat/c", start="origin/feat/b")
    base = advance(work)
    # Opened youngest first, so only the ordering can get this right.
    github.add_pr("feat/c", base="feat/b")
    github.add_pr("feat/b", base="feat/a")
    github.add_pr("feat/a")

    report = run(github)

    assert [row.head for row in report.rows] == ["feat/a", "feat/b", "feat/c"]
    assert set(actions(report).values()) == {Action.UPDATED}
    assert has_base(work, base, "feat/c")


def test_scenario_6_base_retargeted_after_the_parent_merges(work, github):
    push_branch(work, "feat/a")
    push_branch(work, "feat/b", start="origin/feat/a")
    base = advance(work)
    github.add_pr("feat/a", merged=True)
    child = github.add_pr("feat/b", base="feat/a")
    github.retarget(child.number, "dev")

    report = run(github)

    assert [(row.head, row.base, row.action) for row in report.rows] == [
        ("feat/b", "dev", Action.UPDATED)
    ]
    assert has_base(work, base, "feat/b")


def test_scenario_7_head_moves_between_check_and_update(work, clone, github, monkeypatch):
    push_branch(work, "feat/a")
    advance(work)
    github.add_pr("feat/a")
    session = clone("session")
    git(session, "switch", "-q", "feat/a")
    original = github.base_status
    moved = []

    def push_during_check(pr):
        if not moved:
            (session / "concurrent.txt").write_text("local\n", encoding="utf-8")
            git(session, "add", "concurrent.txt")
            git(session, "commit", "-q", "-m", "feat: a concurrent commit")
            git(session, "push", "-q", "origin", "feat/a")
            moved.append(git(session, "rev-parse", "HEAD"))
        return original(pr)

    monkeypatch.setattr(github, "base_status", push_during_check)

    report = run(github)

    assert actions(report) == {"feat/a": Action.REJECTED}
    assert remote_sha(work, "feat/a") == moved[0]
    assert report.changed == []


def test_scenario_9_ineligible_prs_are_skipped_with_reasons(work, github):
    branches = ["feat/draft", "feat/fork", "feat/wip", "release-please--branches--dev"]
    shas = {branch: push_branch(work, branch) for branch in branches}
    advance(work)
    github.add_pr("feat/draft", draft=True)
    github.add_pr("feat/fork", from_fork=True)
    github.add_pr("feat/wip", labels=("wip",))
    github.add_pr("release-please--branches--dev")
    github.add_pr("dev", base="main")

    report = run(github)

    assert {row.head: row.detail for row in report.rows} == {
        "feat/draft": "draft",
        "feat/fork": "from a fork",
        "feat/wip": "labelled wip",
        "release-please--branches--dev": "release-please branch",
        "dev": "head is the long-lived branch dev",
    }
    assert set(actions(report).values()) == {Action.SKIPPED}
    assert {branch: remote_sha(work, branch) for branch in branches} == shas


def test_scenario_10_a_failed_run_is_followed_by_a_clean_one(work, github, monkeypatch):
    push_branch(work, "feat/a")
    push_branch(work, "feat/b")
    base = advance(work)
    github.add_pr("feat/a")
    github.add_pr("feat/b")
    original = github.update_branch
    failures = [GitHubError("gh failed part-way (HTTP 502)")]

    def fail_once(number, expected_head_sha):
        if failures:
            raise failures.pop()
        return original(number, expected_head_sha)

    monkeypatch.setattr(github, "update_branch", fail_once)

    first = run(github)
    assert first.failed
    assert actions(first) == {"feat/a": Action.FAILED, "feat/b": Action.UPDATED}

    second = run(github)
    assert not second.failed
    assert actions(second) == {"feat/a": Action.UPDATED, "feat/b": Action.UP_TO_DATE}
    assert has_base(work, base, "feat/a")
