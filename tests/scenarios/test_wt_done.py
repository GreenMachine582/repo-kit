"""Scenario 11: `wt done` refuses each unsafe state with the matching message."""

from dataclasses import replace

import pytest
from harness import commit, git

from repo_kit import git as git_module
from repo_kit.errors import RkError
from repo_kit.worktree import done, start, sync


@pytest.fixture
def finished(clone, make_ctx, github):
    """A worktree whose work is pushed and whose PR has merged: safe to remove."""
    local = clone("local")
    path = start(make_ctx(local), "feat/x", no_setup=True)
    commit(path, "x.txt", message="feat: add x")
    sync(make_ctx(path))
    github.add_pr("feat/x", merged=True, head_sha=git(path, "rev-parse", "HEAD"))
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
    github.add_pr("feat/x", state="closed", head_sha=git(path, "rev-parse", "HEAD"))
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
    sha = commit(path, "later.txt", message="feat: add later")
    if github.prs:  # the merged PR's head moves with it: only the unpushed commit is in question
        github.prs[0] = replace(github.prs[0], head_sha=sha)


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


# --- review findings: never delete work the checks couldn't see -----------------------------


def set_env_dirs(local, path, make_ctx, entries):
    """Commit a `.repo-kit.toml` with these env_dirs into the worktree."""
    listing = ", ".join(f'"{e}"' for e in entries)
    commit(path, ".repo-kit.toml", f"[worktree]\nenv_dirs = [{listing}]\n", "chore: env dirs")


def refresh_pr(github, path):
    github.prs.clear()
    github.add_pr("feat/x", merged=True, head_sha=git(path, "rev-parse", "HEAD"))


def test_refuses_an_env_dir_that_git_tracks(finished, make_ctx, github):
    local, path = finished
    commit(path, "src/app.py", "print(1)\n", "feat: add app")
    set_env_dirs(local, path, make_ctx, ["src"])
    refresh_pr(github, path)
    (path / "src" / "app.py").write_text("edited, uncommitted\n", encoding="utf-8")

    for flags in ({}, {"discard_changes": True}):  # no override reaches this one
        ctx = make_ctx(local)
        with pytest.raises(RkError, match=r"env_dirs. entry 'src' holds 1 file\(s\) Git tracks"):
            done(ctx, "feat/x", **flags)
        assert ctx.changed == []
    assert (path / "src" / "app.py").read_text(encoding="utf-8") == "edited, uncommitted\n"


def test_refuses_ignored_files_it_would_delete(finished, make_ctx, github):
    local, path = finished
    commit(path, ".gitignore", ".env\n*.db\n__pycache__/\n", "chore: ignore")
    sync(make_ctx(path))
    refresh_pr(github, path)
    (path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (path / "local.db").write_text("rows\n", encoding="utf-8")
    (path / "__pycache__").mkdir()  # a regenerable cache is not work
    (path / "__pycache__" / "m.pyc").write_text("x", encoding="utf-8")

    for flags in ({}, {"discard_changes": True, "allow_unmerged": True}):
        ctx = make_ctx(local)
        with pytest.raises(RkError, match="2 ignored file") as exc:
            done(ctx, "feat/x", **flags)
        assert ".env" in str(exc.value) and "local.db" in str(exc.value)
        assert "__pycache__" not in str(exc.value)
        assert "no override" in str(exc.value)
        assert ctx.changed == []
    assert (path / ".env").exists() and (path / ".venv").exists()

    (path / ".env").unlink()
    (path / "local.db").unlink()
    done(make_ctx(local), "feat/x")
    assert not path.exists()


def test_a_locked_env_dir_is_an_error_not_a_traceback(finished, make_ctx, monkeypatch):
    local, path = finished

    def locked(target):
        raise PermissionError(13, "Access is denied", str(target / "Lib" / "python.exe"))

    monkeypatch.setattr("repo_kit.worktree._rmtree", locked)
    ctx = make_ctx(local)
    with pytest.raises(RkError, match=r"(?s)couldn't remove .*python\.exe.*Close whatever"):
        done(ctx, "feat/x")
    assert path.exists() and (path / ".venv").exists()
    monkeypatch.undo()
    done(make_ctx(local), "feat/x")  # the rerun needs no override
    assert not path.exists()


def test_a_removal_that_stops_partway_can_be_rerun(finished, make_ctx, monkeypatch):
    local, path = finished
    real_remove = git_module.worktree_remove

    def fails_after_deleting(cwd, target, force=False):
        (target / "x.txt").unlink()  # Git got this far before a lock stopped it
        raise git_module.GitError("Permission denied")

    monkeypatch.setattr(git_module, "worktree_remove", fails_after_deleting)
    with pytest.raises(RkError, match="run `rk wt done` again"):
        done(make_ctx(local), "feat/x")
    assert path.exists() and git(path, "status", "--porcelain") == "D x.txt"

    monkeypatch.setattr(git_module, "worktree_remove", real_remove)
    done(make_ctx(local), "feat/x")  # no --discard-changes
    assert not path.exists()
    assert git(local, "branch", "--list", "feat/x") == ""


def test_the_rerun_still_refuses_real_changes(finished, make_ctx, monkeypatch):
    local, path = finished

    def fails_after_deleting(cwd, target, force=False):
        (target / "x.txt").unlink()
        raise git_module.GitError("Permission denied")

    monkeypatch.setattr(git_module, "worktree_remove", fails_after_deleting)
    with pytest.raises(RkError):
        done(make_ctx(local), "feat/x")
    monkeypatch.undo()
    (path / "new.txt").write_text("new work\n", encoding="utf-8")
    with pytest.raises(RkError, match="1 uncommitted or untracked"):
        done(make_ctx(local), "feat/x")
    assert (path / "new.txt").exists()


def test_a_worktree_whose_folder_is_already_gone_finishes(finished, make_ctx):
    from repo_kit.worktree import _rmtree

    local, path = finished
    _rmtree(path)
    done(make_ctx(local), "feat/x")
    assert git(local, "branch", "--list", "feat/x") == ""
    assert "feat/x" not in git(local, "worktree", "list")


def test_unpushed_commits_survive_a_branch_named_like_a_tag(finished, make_ctx):
    local, path = finished
    commit(path, "later.txt", message="feat: later")
    git(local, "tag", "feat/x", "origin/dev")  # `git log feat/x` would read the tag
    with pytest.raises(RkError, match=r"1 unpushed commit\(s\)"):
        done(make_ctx(local), "feat/x")
    assert path.exists()


def test_an_old_merged_pr_of_a_reused_branch_name_does_not_count(finished, make_ctx, github):
    local, path = finished
    old_head = github.prs[0].head_sha
    commit(path, "again.txt", message="feat: new work on the reused name")
    sync(make_ctx(path))  # pushed, so only the PR check can object
    github.prs[0] = replace(github.prs[0], head_sha=old_head)

    ctx = make_ctx(local)
    with pytest.raises(RkError, match=r"PR #1 is merged but doesn't contain the latest commit"):
        done(ctx, "feat/x")
    assert path.exists() and ctx.changed == []

    refresh_pr(github, path)  # the new PR for the new work merged
    done(make_ctx(local), "feat/x")
    assert not path.exists()
