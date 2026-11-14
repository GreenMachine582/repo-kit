"""`rk branches prune`, against a real origin and clone, with PRs in the fake store."""

from __future__ import annotations

import pytest
from harness import commit, git, push_branch, remote_sha

from repo_kit import branches
from repo_kit.config import RepoConfig

CONFIG = RepoConfig()


@pytest.fixture
def work(clone):
    path = clone("work")
    git(path, "switch", "-q", "dev")
    return path


def feature(work, name, github, **pr):
    """Push `name` with one commit, record a PR for it at that head, and go back to `dev`."""
    sha = push_branch(work, name)
    if pr:
        github.add_pr(name, head_sha=sha, **pr)
    git(work, "switch", "-q", "dev")
    return sha


def prune(work, github, apply=False):
    said: list[str] = []
    changed: list[str] = []
    code = branches.prune(work, CONFIG, github, apply=apply, changed=changed, say=said.append)
    return code, said, changed


def refs(work):
    return git(work, "for-each-ref", "--format=%(refname)", "refs/heads"), git(
        work, "ls-remote", "--heads", "origin"
    )


def test_a_dry_run_lists_and_deletes_nothing(work, github):
    feature(work, "feat/done", github, merged=True)
    before = refs(work)

    code, said, changed = prune(work, github)

    assert code == 0
    assert "would delete  feat/done (PR merged)" in said
    assert "would delete  origin/feat/done (PR merged)" in said
    assert said[-1] == "Dry run. Rerun with --apply to delete."
    assert changed == []
    assert refs(work) == before


def test_a_merged_branch_goes_locally_and_on_origin(work, github):
    feature(work, "feat/done", github, merged=True)

    code, said, changed = prune(work, github, apply=True)

    assert code == 0
    assert changed == ["deleted feat/done (PR merged)", "deleted origin/feat/done (PR merged)"]
    assert "feat/done" not in git(work, "branch", "--list", "feat/done")
    assert remote_sha(work, "feat/done") is None
    assert "Dry run" not in "\n".join(said)


def test_a_local_commit_beyond_the_merged_pr_is_kept(work, github):
    feature(work, "feat/more", github, merged=True)
    git(work, "switch", "-q", "feat/more")
    commit(work, "later.txt")
    git(work, "switch", "-q", "dev")

    _code, said, changed = prune(work, github, apply=True)

    assert "kept          feat/more (PR newer)" in said
    assert changed == ["deleted origin/feat/more (PR merged)"]


def test_open_and_prless_branches_are_kept(work, github):
    feature(work, "feat/open", github, state="open")
    feature(work, "feat/nopr", github)

    _code, said, changed = prune(work, github, apply=True)

    assert changed == []
    assert "kept          feat/open (PR open)" in said
    assert "kept          origin/feat/nopr (PR none)" in said


def test_a_closed_pr_clears_origin_but_keeps_the_local_branch(work, github):
    feature(work, "feat/dropped", github, state="closed")

    _code, said, changed = prune(work, github, apply=True)

    assert "kept          feat/dropped (PR closed)" in said
    assert changed == ["deleted origin/feat/dropped (PR closed)"]


def test_long_lived_and_checked_out_branches_are_kept(work, github, tmp_path):
    feature(work, "feat/here", github, merged=True)
    feature(work, "feat/there", github, merged=True)
    git(work, "switch", "-q", "feat/here")
    other = tmp_path / "other"
    git(work, "worktree", "add", "-q", str(other), "feat/there")

    _code, said, changed = prune(work, github, apply=True)

    assert any(line.startswith("kept          dev (long-lived)") for line in said)
    assert not any("origin/dev" in line or "origin/main" in line for line in said)
    assert "kept          feat/here (checked out)" in said
    assert any(
        line.startswith("kept          feat/there (checked out in") and "rk wt done" in line
        for line in said
    )
    assert sorted(changed) == [
        "deleted origin/feat/here (PR merged)",
        "deleted origin/feat/there (PR merged)",
    ]


def test_a_pr_head_that_isnt_here_keeps_the_branch(work, github):
    push_branch(work, "feat/elsewhere")
    git(work, "switch", "-q", "dev")
    github.add_pr("feat/elsewhere", merged=True, head_sha="0" * 40)

    _code, said, changed = prune(work, github, apply=True)

    assert "kept          feat/elsewhere (PR newer)" in said
    assert changed == []


def test_a_refused_deletion_fails_the_run(work, github, monkeypatch):
    feature(work, "feat/done", github, merged=True)

    def refuse(_cwd, _branch, remote="origin"):
        return branches.git.PushResult(False, "remote rejected (protected branch)")

    monkeypatch.setattr(branches.git, "delete_remote_branch", refuse)
    code, said, changed = prune(work, github, apply=True)

    assert code == 1
    assert "FAILED        origin/feat/done: remote rejected (protected branch)" in said
    assert changed == ["deleted feat/done (PR merged)"]
