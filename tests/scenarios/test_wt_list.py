"""`wt list`: each worktree of this repo, its PR and its unsaved work. Reads only."""

import shutil

import pytest
from fakes import FakeGitHub
from harness import commit, git

from repo_kit.cli import main
from repo_kit.config import UserConfig
from repo_kit.github import GitHubError, NoGitHub
from repo_kit.worktree import Context, list_worktrees, start, sync, worktree_statuses


def listed(ctx):
    said: list[str] = []
    ctx.say = said.append
    assert list_worktrees(ctx) == 0
    return said


def row_text(said, branch):
    (line,) = [line for line in said if line.startswith(f"{branch} ")]
    return line


def row(said, branch):
    return row_text(said, branch).split()


@pytest.fixture
def local(clone):
    return clone("local")


def test_lists_each_pr_state_and_local_flags(local, make_ctx, github):
    open_path = start(make_ctx(local), "feat/open", no_setup=True)
    commit(open_path, "o.txt", message="feat: add o")
    sync(make_ctx(open_path))
    github.add_pr("feat/open")
    start(make_ctx(local), "feat/draft", no_setup=True)
    github.add_pr("feat/draft", draft=True)
    start(make_ctx(local), "fix/merged", no_setup=True)
    github.add_pr("fix/merged", merged=True)
    start(make_ctx(local), "fix/closed", no_setup=True)
    github.add_pr("fix/closed", state="closed")
    dirty = start(make_ctx(local), "feat/dirty", no_setup=True)
    (dirty / "README.md").write_text("changed\n", encoding="utf-8")
    (dirty / "new.txt").write_text("new\n", encoding="utf-8")
    (dirty / ".venv").mkdir()  # an env dir is not unsaved work
    (dirty / ".venv" / "x").write_text("x", encoding="utf-8")
    unpushed = start(make_ctx(local), "feat/unpushed", no_setup=True)
    commit(unpushed, "u1.txt", message="feat: add u1")
    commit(unpushed, "u2.txt", message="feat: add u2")

    said = listed(make_ctx(local))

    assert said[0].split() == ["BRANCH", "PR", "LOCAL", "PATH"]
    assert len(said) == 7  # the header and six worktrees; the main checkout isn't one
    assert row(said, "feat/open") == ["feat/open", "#1", "open", "clean", str(open_path)]
    assert row(said, "feat/draft")[1:4] == ["#2", "draft", "clean"]
    assert row(said, "fix/merged")[1:3] == ["#3", "merged"]
    assert row(said, "fix/closed")[1:3] == ["#4", "closed"]
    assert row(said, "feat/dirty") == ["feat/dirty", "none", "2", "uncommitted", str(dirty)]
    assert row(said, "feat/unpushed")[1:4] == ["none", "2", "unpushed"]
    # Aligned: every path starts in the same column.
    assert len({line.rindex(line.split()[-1]) for line in said}) == 1


def test_uncommitted_and_unpushed_together(local, make_ctx):
    path = start(make_ctx(local), "feat/x", no_setup=True)
    commit(path, "x.txt", message="feat: add x")
    (path / "y.txt").write_text("y\n", encoding="utf-8")

    said = listed(make_ctx(local))

    assert "1 uncommitted, 1 unpushed" in row_text(said, "feat/x")


def test_missing_directory_is_flagged(local, make_ctx):
    path = start(make_ctx(local), "feat/gone", no_setup=True)
    commit(path, "x.txt", message="feat: add x")
    shutil.rmtree(path)

    said = listed(make_ctx(local))

    assert "directory missing, 1 unpushed" in row_text(said, "feat/gone")


def test_writes_and_fetches_nothing(local, make_ctx, github, clone):
    path = start(make_ctx(local), "feat/x", no_setup=True)
    (path / "y.txt").write_text("y\n", encoding="utf-8")
    other = clone("other")
    commit(other, "later.txt", message="feat: add later")
    git(other, "push", "-q", "origin", "HEAD:dev")
    before = (git(local, "for-each-ref"), git(path, "status", "--porcelain"))

    listed(make_ctx(local))

    assert (git(local, "for-each-ref"), git(path, "status", "--porcelain")) == before


def test_no_worktrees(local, make_ctx, wt_root):
    said = listed(make_ctx(local))
    assert said == [f"No worktrees under {wt_root / 'local-origin'}"]


def test_origin_not_on_github_says_prs_are_unknown(local, make_ctx, wt_root):
    start(make_ctx(local), "feat/x", no_setup=True)
    ctx = Context.load(local, github=NoGitHub(), user_config=UserConfig(worktree_root=wt_root))

    said = listed(ctx)

    assert row(said, "feat/x")[1:3] == ["?", "clean"]
    assert said[-1] == "PRs unknown: origin isn't on GitHub"


class FailingGitHub(FakeGitHub):
    def pr_for_branch(self, branch):
        raise GitHubError("gh api failed: HTTP 401")


def test_gh_failure_still_lists_the_worktrees(local, make_ctx, wt_root, origin):
    path = start(make_ctx(local), "feat/x", no_setup=True)
    ctx = Context.load(
        local, github=FailingGitHub(origin), user_config=UserConfig(worktree_root=wt_root)
    )

    said = listed(ctx)

    assert row(said, "feat/x") == ["feat/x", "?", "clean", str(path)]
    assert said[-2:] == ["PRs unknown: can't look them up", "gh api failed: HTTP 401"]


def test_runs_from_inside_a_worktree(local, make_ctx, github):
    path = start(make_ctx(local), "feat/x", no_setup=True)
    github.add_pr("feat/x")
    statuses, note = worktree_statuses(make_ctx(path))
    assert [(s.branch, s.pr and s.pr.number) for s in statuses] == [("feat/x", 1)]
    assert note == ""


def test_cli_exits_0_and_prints_no_changed_list(
    local, make_ctx, wt_root, tmp_path, monkeypatch, capsys
):
    start(make_ctx(local), "feat/x", no_setup=True)
    config = tmp_path / "user.toml"
    config.write_text(f"worktree_root = '{wt_root.as_posix()}'\n", encoding="utf-8")
    monkeypatch.setenv("REPO_KIT_CONFIG", str(config))
    monkeypatch.chdir(local)

    assert main(["wt", "list"]) == 0

    out = capsys.readouterr().out
    assert "feat/x" in out
    assert "PRs unknown: origin isn't on GitHub" in out  # a local bare origin
    assert "Changed:" not in out
