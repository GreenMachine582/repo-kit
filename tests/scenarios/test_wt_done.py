"""Scenario 11: `wt done` refuses each unsafe state with the matching message."""

import pytest
from harness import commit, git

from repo_kit.errors import RkError
from repo_kit.worktree import done, start, sync


@pytest.fixture
def finished(clone, make_ctx, github):
    """A worktree whose work is pushed and whose PR has merged: safe to remove."""
    local = clone("local")
    path = start(make_ctx(local), "feat/x", no_setup=True)
    commit(path, "x.txt", message="feat: add x")
    sync(make_ctx(path))
    github.add_pr("feat/x", merged=True)
    (path / ".venv" / "Lib").mkdir(parents=True)
    (path / ".venv" / "Lib" / "ro.txt").write_text("x", encoding="utf-8")
    (path / ".venv" / "Lib" / "ro.txt").chmod(0o444)  # read-only, as venvs on Windows have
    return local, path


def test_happy_path_removes_everything(finished, make_ctx):
    local, path = finished
    ctx = make_ctx(local)
    done(ctx, "feat/x")

    assert not path.exists()
    assert git(local, "branch", "--list", "feat/x") == ""
    assert "feat/x" not in git(local, "worktree", "list")
    assert ctx.changed == [
        f"removed {path / '.venv'}",
        f"removed worktree {path}",
        "deleted local branch feat/x",
    ]


def test_merged_branch_deleted_from_origin_is_not_unpushed(finished, clone, make_ctx):
    local, path = finished
    # GitHub merges the PR into dev and deletes the branch.
    other = clone("other")
    git(other, "switch", "-q", "dev")
    git(other, "merge", "-q", "--no-edit", "origin/feat/x")
    git(other, "push", "-q", "origin", "HEAD:dev", ":feat/x")
    done(make_ctx(local), "feat/x")
    assert not path.exists()


def test_removes_links(clone, make_ctx, github, wt_root):
    from repo_kit.worktree import worktree_path

    local = clone("local")
    worktree_path(wt_root, "local", "core", "feat/y").mkdir(parents=True)
    path = start(make_ctx(local), "feat/x", links=["core=feat/y"], no_setup=True)
    github.add_pr("feat/x", state="closed")
    ctx = make_ctx(local)
    done(ctx, "feat/x")
    assert "removed links (.repo-kit.local.toml)" in ctx.changed
    assert not path.exists()


def make_open_pr(local, path, github):
    github.add_pr("feat/x")


def make_no_pr(local, path, github):
    pass


def make_uncommitted(local, path, github):
    (path / "README.md").write_text("changed\n", encoding="utf-8")
    (path / "new.txt").write_text("new\n", encoding="utf-8")


def make_unpushed(local, path, github):
    commit(path, "later.txt", message="feat: add later")


UNSAFE = [
    (make_open_pr, "PR #2 is still open (--allow-unmerged", "allow_unmerged"),
    (make_no_pr, "no PR was found (--allow-unmerged", "allow_unmerged"),
    (
        make_uncommitted,
        "2 uncommitted or untracked change(s) (--discard-changes",
        "discard_changes",
    ),
    (make_unpushed, "1 unpushed commit(s) (--delete-unpushed", "delete_unpushed"),
]


@pytest.mark.parametrize(
    ("make", "message", "override"), UNSAFE, ids=lambda v: getattr(v, "__name__", v)
)
def test_scenario_11_refuses_each_unsafe_state(finished, make_ctx, github, make, message, override):
    local, path = finished
    if make is make_no_pr or make is make_open_pr:
        github.prs.clear()
        github.add_pr("feat/other")  # PR #1 belongs to another branch
    make(local, path, github)

    ctx = make_ctx(local)
    with pytest.raises(RkError, match="refusing to remove feat/x") as exc:
        done(ctx, "feat/x")
    assert message in str(exc.value)
    assert ctx.changed == []
    assert path.exists()

    # Its own override, and only that one, lets it through.
    done(make_ctx(local), "feat/x", **{override: True})
    assert not path.exists()


def test_all_problems_are_reported_together(finished, make_ctx, github):
    local, path = finished
    github.prs.clear()
    make_uncommitted(local, path, github)
    make_unpushed(local, path, github)
    with pytest.raises(RkError) as exc:
        done(make_ctx(local), "feat/x")
    text = str(exc.value)
    assert "no PR was found" in text and "uncommitted" in text and "unpushed" in text


def test_refuses_a_worktree_of_another_repo(finished, clone, make_ctx):
    _, path = finished
    elsewhere = clone("elsewhere")
    with pytest.raises(RkError, match="has no worktree in local/origin"):
        done(make_ctx(elsewhere), "feat/x", allow_unmerged=True)
    assert path.exists()


def test_refuses_a_worktree_rk_did_not_create(clone, make_ctx, tmp_path):
    local = clone("local")
    manual = tmp_path / "manual"
    git(local, "worktree", "add", "-q", "-b", "feat/manual", str(manual), "origin/dev")
    with pytest.raises(RkError, match="isn't under"):
        done(make_ctx(local), "feat/manual", allow_unmerged=True)
    assert manual.exists()


def test_refuses_from_inside_the_worktree(finished, make_ctx):
    _, path = finished
    with pytest.raises(RkError, match="from outside the worktree"):
        done(make_ctx(path))
    assert path.exists()


def test_refuses_the_main_checkout(clone, make_ctx):
    local = clone("local")
    with pytest.raises(RkError, match="main checkout"):
        done(make_ctx(local), "main", allow_unmerged=True)
