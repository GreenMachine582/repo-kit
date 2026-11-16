import sys

import pytest
from harness import commit, contains, git, remote_sha

from repo_kit.errors import RkError
from repo_kit.worktree import start, sync


@pytest.fixture
def session(clone, make_ctx):
    """A local clone with a worktree for feat/x holding one pushed commit."""
    local = clone("local")
    path = start(make_ctx(local), "feat/x", no_setup=True)
    commit(path, "x.txt", message="feat: add x")
    sync(make_ctx(path))
    return local, path


def advance_dev(clone, name: str = "other", file: str = "dev.txt") -> str:
    other = clone(name)
    git(other, "switch", "-q", "dev")
    sha = commit(other, file, f"dev {file}\n", f"feat: add {file}")
    git(other, "push", "-q", "origin", "HEAD:dev")
    return sha


def test_first_sync_pushes_the_branch(session):
    local, path = session
    assert remote_sha(local, "feat/x") == git(path, "rev-parse", "HEAD")


def test_scenario_8_bot_updated_branch_and_diverged_session(session, clone, make_ctx, github):
    local, path = session

    # The PR bot merges dev into feat/x on GitHub...
    dev_1 = advance_dev(clone, "other1", "dev1.txt")
    bot = clone("bot")
    git(bot, "switch", "-q", "feat/x")
    git(bot, "merge", "--no-edit", "-q", "origin/dev")
    git(bot, "push", "-q", "origin", "feat/x")
    bot_merge = git(bot, "rev-parse", "HEAD")
    # ...dev moves on again, and the local session commits without knowing about either.
    dev_2 = advance_dev(clone, "other2", "dev2.txt")
    local_commit = commit(path, "y.txt", message="feat: add y")
    pr = github.add_pr("feat/x", base="dev", labels=("needs-merge", "other"))

    ctx = make_ctx(path)
    sync(ctx)

    head = remote_sha(local, "feat/x")
    assert head == git(path, "rev-parse", "HEAD")
    git(path, "fetch", "-q")
    for sha in (bot_merge, dev_1, dev_2, local_commit):
        assert contains(path, sha, head)
    assert git(path, "status", "--porcelain") == ""
    assert github.removed_labels == [(pr.number, "needs-merge")]
    assert github.get(pr.number).labels == ("other",)
    assert ctx.changed == [
        "merged origin/feat/x into feat/x",
        "merged origin/dev into feat/x",
        "pushed feat/x to origin",
        f"removed needs-merge from PR #{pr.number}",
    ]


def test_uses_the_prs_live_base(session, clone, make_ctx, github):
    local, path = session
    other = clone("other")
    git(other, "switch", "-q", "-c", "feat/base", "origin/dev")
    base_commit = commit(other, "base.txt")
    git(other, "push", "-q", "origin", "feat/base")
    github.add_pr("feat/x", base="feat/base")

    sync(make_ctx(path))
    assert contains(path, base_commit, "HEAD")


def test_nothing_to_do_changes_nothing(session, make_ctx):
    _, path = session
    ctx = make_ctx(path)
    sync(ctx)
    assert ctx.changed == []


def test_refuses_uncommitted_or_untracked(session, make_ctx):
    _, path = session
    (path / "scratch.txt").write_text("wip", encoding="utf-8")
    with pytest.raises(RkError, match="uncommitted or untracked") as exc:
        sync(make_ctx(path))
    assert "scratch.txt" in str(exc.value)


def test_conflict_lists_files_and_pushes_nothing(session, clone, make_ctx):
    local, path = session
    advance_dev(clone, "other", "x.txt")  # dev adds x.txt with different content
    before = remote_sha(local, "feat/x")

    ctx = make_ctx(path)
    with pytest.raises(RkError, match=r"conflicted in:\n  x\.txt") as exc:
        sync(ctx)
    assert "commit the merge, then run `rk wt sync` again" in str(exc.value)
    assert remote_sha(local, "feat/x") == before
    assert ctx.changed == []

    # Resolve, commit, sync again: done.
    (path / "x.txt").write_text("both\n", encoding="utf-8")
    git(path, "add", "x.txt")
    git(path, "commit", "-q", "--no-edit")
    sync(make_ctx(path))
    assert remote_sha(local, "feat/x") == git(path, "rev-parse", "HEAD")


def test_failed_check_pushes_nothing(clone, make_ctx):
    local = clone("local")
    python = sys.executable.replace("\\", "/")
    commit(local, ".repo-kit.toml", f"[worktree]\ncheck = '{python} -c \"raise SystemExit(1)\"'\n")
    git(local, "push", "-q", "origin", "HEAD:dev")
    path = start(make_ctx(local), "feat/x", no_setup=True)
    commit(path, "x.txt")

    with pytest.raises(RkError, match="check .* failed"):
        sync(make_ctx(path))
    assert remote_sha(local, "feat/x") is None


def test_rejected_push_says_to_sync_again(session, clone, make_ctx, monkeypatch):
    local, path = session
    commit(path, "y.txt")
    # Someone pushes to feat/x between our fetch and our push.
    racer = clone("racer")
    git(racer, "switch", "-q", "feat/x")
    commit(racer, "z.txt")

    from repo_kit import git as rk_git

    real_push = rk_git.push

    def racing_push(cwd, branch, remote="origin"):
        git(racer, "push", "-q", "origin", "feat/x")
        return real_push(cwd, branch, remote)

    monkeypatch.setattr(rk_git, "push", racing_push)
    with pytest.raises(RkError, match="rejected .* Run `rk wt sync` again"):
        sync(make_ctx(path))
