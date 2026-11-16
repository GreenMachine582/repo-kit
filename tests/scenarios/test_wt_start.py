import sys

import pytest
from harness import commit, git

from repo_kit.cli import main
from repo_kit.errors import RkError
from repo_kit.worktree import LOCAL_CONFIG_NAME, start, worktree_path


def test_start_branches_off_dev(clone, make_ctx, wt_root):
    local = clone("local")
    ctx = make_ctx(local)
    path = start(ctx, "feat/x", no_setup=True)

    assert path == worktree_path(wt_root, "local", "origin", "feat/x")
    assert path.is_dir()
    assert git(path, "branch", "--show-current") == "feat/x"
    assert git(path, "rev-parse", "HEAD") == git(local, "rev-parse", "origin/dev")
    assert git(local, "config", "branch.feat/x.rkBase") == "dev"
    # The new branch must not track its base, or a bare `git push` would go to dev.
    assert git(path, "config", "--default", "", "branch.feat/x.merge") == ""
    assert ctx.changed == ["created branch feat/x from origin/dev", f"added worktree {path}"]


def test_scenario_12_colliding_slugs_get_distinct_paths(clone, make_ctx):
    local = clone("local")
    a = start(make_ctx(local), "feat/api-v2", no_setup=True)
    b = start(make_ctx(local), "feat/api/v2", no_setup=True)

    assert a != b
    assert a.parent == b.parent
    assert a.name.startswith("feat-api-v2-") and b.name.startswith("feat-api-v2-")
    assert git(a, "branch", "--show-current") == "feat/api-v2"
    assert git(b, "branch", "--show-current") == "feat/api/v2"


def test_from_chains_on_another_branch(clone, make_ctx):
    local = clone("local")
    parent = start(make_ctx(local), "feat/parent", no_setup=True)
    parent_head = commit(parent, "parent.txt")  # not pushed: chains on the local branch

    child = start(make_ctx(local), "feat/child", from_branch="feat/parent", no_setup=True)
    assert git(child, "rev-parse", "HEAD") == parent_head
    assert git(local, "config", "branch.feat/child.rkBase") == "feat/parent"


def test_refuses_existing_branch(clone, make_ctx):
    local = clone("local")
    start(make_ctx(local), "feat/x", no_setup=True)
    with pytest.raises(RkError, match="already exists"):
        start(make_ctx(local), "feat/x", no_setup=True)


def test_refuses_missing_base(clone, make_ctx, wt_root):
    local = clone("local")
    with pytest.raises(RkError, match="base branch feat/nope not found"):
        start(make_ctx(local), "feat/x", from_branch="feat/nope", no_setup=True)
    assert not wt_root.exists()


def test_setup_runs_in_the_worktree(clone, make_ctx):
    local = clone("local")
    python = sys.executable.replace("\\", "/")
    commit(
        local,
        ".repo-kit.toml",
        f"[worktree]\nsetup = ['''{python} -c \"open('made.txt', 'w').write('ok')\"''']\n",
    )
    git(local, "push", "-q", "origin", "HEAD:dev")

    path = start(make_ctx(local), "feat/x")
    assert (path / "made.txt").read_text() == "ok"


def test_setup_runs_shell_scripts(clone, make_ctx):
    local = clone("local")
    commit(local, "scripts/setup.sh", "#!/usr/bin/env bash\necho ok > made-by-bash.txt\n")
    commit(local, ".repo-kit.toml", '[worktree]\nsetup = ["./scripts/setup.sh"]\n')
    git(local, "push", "-q", "origin", "HEAD:dev")

    path = start(make_ctx(local), "feat/x")
    assert (path / "made-by-bash.txt").read_text().strip() == "ok"


def test_failed_setup_reports_and_keeps_the_worktree(clone, make_ctx):
    local = clone("local")
    python = sys.executable.replace("\\", "/")
    commit(
        local, ".repo-kit.toml", f"[worktree]\nsetup = ['{python} -c \"raise SystemExit(3)\"']\n"
    )
    git(local, "push", "-q", "origin", "HEAD:dev")

    ctx = make_ctx(local)
    with pytest.raises(RkError, match=r"setup .* failed \(exit 3\)"):
        start(ctx, "feat/x")
    assert any(item.startswith("added worktree") for item in ctx.changed)


def test_link_writes_local_config_and_keeps_status_clean(clone, make_ctx, wt_root):
    local = clone("local")
    core = worktree_path(wt_root, "local", "greentechhub-core", "feat/y")
    core.mkdir(parents=True)

    path = start(make_ctx(local), "feat/x", links=["greentechhub-core=feat/y"], no_setup=True)
    text = (path / LOCAL_CONFIG_NAME).read_text(encoding="utf-8")
    assert f'"greentechhub-core" = "{core.as_posix()}"' in text
    assert git(path, "status", "--porcelain") == ""


def test_link_to_missing_worktree_creates_nothing(clone, make_ctx, wt_root):
    local = clone("local")
    with pytest.raises(RkError, match="no worktree at"):
        start(make_ctx(local), "feat/x", links=["greentechhub-core=feat/y"], no_setup=True)
    assert not worktree_path(wt_root, "local", "origin", "feat/x").exists()
    assert git(local, "branch", "--list", "feat/x") == ""


def test_cli_reports_refusals(clone, monkeypatch, capsys):
    local = clone("local")
    monkeypatch.chdir(local)
    assert main(["wt", "start", "nope"]) == 1
    captured = capsys.readouterr()
    assert "invalid branch name 'nope'" in captured.err
    assert "Changed: nothing" in captured.out
