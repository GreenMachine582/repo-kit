"""The fake PR store's updates are real merges in the bare origin, as `update-prs` will rely on."""

from __future__ import annotations

from harness import advance, commit, contains, git, push_branch, remote_sha

from repo_kit.github import BaseStatus


def _open_pr(github, branch):
    github.add_pr(branch)
    return github.list_open_prs()[-1]


def test_a_clean_update_merges_the_base_in(clone, github):
    work = clone("work")
    head = push_branch(work, "feat/a")
    base = advance(work)
    pr = _open_pr(github, "feat/a")
    assert pr.head_sha == head
    assert github.base_status(pr) is BaseStatus.BEHIND

    assert github.update_branch(pr.number, pr.head_sha).ok

    git(work, "fetch", "-q", "origin")
    assert contains(work, head, "origin/feat/a")
    assert contains(work, base, "origin/feat/a")
    assert remote_sha(work, "feat/a") != head
    assert github.base_status(github.list_open_prs()[-1]) is BaseStatus.CURRENT


def test_a_conflict_is_refused_and_leaves_the_head_alone(clone, github):
    work = clone("work")
    head = push_branch(work, "feat/a", filename="same.txt", content="ours\n")
    advance(work, filename="same.txt", content="theirs\n")
    pr = _open_pr(github, "feat/a")
    assert github.base_status(pr) is BaseStatus.CONFLICTING

    assert not github.update_branch(pr.number, pr.head_sha).ok
    assert remote_sha(work, "feat/a") == head


def test_a_moved_head_is_refused_and_nothing_is_lost(clone, github):
    work = clone("work")
    stale = push_branch(work, "feat/a")
    pr = _open_pr(github, "feat/a")
    newer = commit(work, "second.txt")
    git(work, "push", "-q", "origin", "feat/a")
    advance(work)

    assert not github.update_branch(pr.number, stale).ok
    assert remote_sha(work, "feat/a") == newer


def test_a_branch_with_the_base_already_in_is_current(clone, github):
    work = clone("work")
    push_branch(work, "feat/a")
    assert github.base_status(_open_pr(github, "feat/a")) is BaseStatus.CURRENT


def test_labels_and_the_marked_comment(clone, github):
    work = clone("work")
    push_branch(work, "feat/a")
    pr = _open_pr(github, "feat/a")

    github.add_label(pr.number, "needs-merge")
    github.add_label(pr.number, "needs-merge")
    assert github.get(pr.number).labels == ("needs-merge",)

    github.upsert_comment(pr.number, "<!-- m -->", "first")
    github.upsert_comment(pr.number, "<!-- m -->", "second")
    assert github.comments[pr.number] == ["<!-- m -->\nsecond"]
