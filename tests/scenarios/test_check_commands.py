"""`rk check commits`, `rk check commit-msg` and `rk install --hooks`, against real Git."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
from harness import commit, git

import repo_kit
from repo_kit import bootstrap, checks
from repo_kit.errors import RkError
from repo_kit.git import run as git_run

HOOK = Path(".git/hooks/commit-msg")


@pytest.fixture(autouse=True)
def released_engine(monkeypatch):
    monkeypatch.setattr(repo_kit, "__version__", "1.4.0")


def install(work, github, hooks=True):
    changed: list[str] = []
    bootstrap.install(
        work,
        "local",
        "demo",
        github,
        profile="dev-main",
        changed=changed,
        hooks=hooks,
        confirm=lambda _q: False,
        say=lambda _m: None,
    )
    return changed


def test_commits_reports_each_bad_commit_and_skips_merges(clone):
    work = clone("work")
    git(work, "switch", "-q", "-c", "feat/x")
    commit(work, "a.txt", message="feat: add a")
    commit(work, "b.txt", message="Add b")
    git(work, "switch", "-q", "main")
    commit(work, "c.txt", message="fix: c on main")
    git(work, "switch", "-q", "feat/x")
    git(work, "merge", "-q", "--no-edit", "main")
    said: list[str] = []

    code = checks.run_commits(work, "origin/main..feat/x", say=said.append)

    assert code == 1
    bad = [line for line in said if "not a conventional commit" in line]
    assert len(bad) == 1
    assert bad[0].endswith(": Add b")
    assert not any("Merge" in line for line in said[:-1])
    assert said[-1].startswith("1 problem(s) in 3 commit(s)")


def test_commits_passes_a_clean_range(clone):
    work = clone("work")
    commit(work, "a.txt", message="feat: add a")
    said: list[str] = []
    assert checks.run_commits(work, "origin/main..HEAD", say=said.append) == 0
    assert said == ["1 commit(s) OK"]


def test_commit_msg_checks_the_file(clone, tmp_path):
    work = clone("work")
    message = tmp_path / "MSG"
    message.write_text("# comment\nfeat: x\n", encoding="utf-8")
    assert checks.run_commit_msg(work, message, say=lambda _m: None) == 0
    message.write_text("wip\n", encoding="utf-8")
    said: list[str] = []
    assert checks.run_commit_msg(work, message, say=said.append) == 1
    assert said[0].startswith("commit-msg: not a conventional commit")
    assert said[-1].startswith("Types: feat, fix")


def test_install_hooks_writes_an_executable_hook_once(clone, github):
    work = clone("work")

    changed = install(work, github)

    hook = work / HOOK
    assert hook.read_text(encoding="utf-8") == bootstrap.HOOK_TEXT
    assert f"installed the commit-msg hook at {hook}" in changed
    if os.name != "nt":
        assert hook.stat().st_mode & stat.S_IXUSR
    assert install(work, github) == []


def test_without_hooks_no_hook_is_written(clone, github):
    work = clone("work")
    install(work, github, hooks=False)
    assert not (work / HOOK).exists()


def test_an_older_rk_hook_is_backed_up_and_replaced(clone, github):
    work = clone("work")
    old = f"#!/bin/sh\n{bootstrap.HOOK_MARKER}: an older version\nexec rk check commit-msg $1\n"
    (work / HOOK).write_text(old, encoding="utf-8")

    changed = install(work, github)

    assert (work / HOOK).read_text(encoding="utf-8") == bootstrap.HOOK_TEXT
    assert f"updated the commit-msg hook at {work / HOOK}" in changed
    (backup_line,) = [c for c in changed if c.startswith("backed up the old commit-msg hook")]
    assert Path(backup_line.rsplit(" to ", 1)[1]).read_text(encoding="utf-8") == old


def test_another_tools_hook_is_refused_before_anything_is_written(clone, github):
    work = clone("work")
    foreign = "#!/bin/sh\nnpx commitlint --edit $1\n"
    (work / HOOK).write_text(foreign, encoding="utf-8")

    with pytest.raises(RkError, match="another tool's commit-msg hook"):
        install(work, github)

    assert (work / HOOK).read_text(encoding="utf-8") == foreign
    assert git(work, "status", "--porcelain") == ""


def test_the_installed_hook_runs_the_rules_on_a_real_commit(clone, github, monkeypatch):
    # The hook calls `rk` from PATH: put this environment's scripts first.
    scripts = Path(sys.executable).parent
    monkeypatch.setenv("PATH", f"{scripts}{os.pathsep}{os.environ['PATH']}")
    work = clone("work")
    install(work, github)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "chore: adopt repo-kit")

    (work / "a.txt").write_text("a\n", encoding="utf-8")
    git(work, "add", "a.txt")
    refused = git_run(["commit", "-q", "-m", "add a"], work, check=False)
    assert refused.returncode != 0
    assert "not a conventional commit" in refused.stderr + refused.stdout

    assert git_run(["commit", "-q", "-m", "feat: add a"], work, check=False).returncode == 0

    # A merge commit's default message isn't conventional, and is let through.
    git(work, "switch", "-q", "-c", "side", "HEAD~1")
    commit(work, "side.txt", message="feat: side")
    git(work, "switch", "-q", "main")
    assert git_run(["merge", "--no-ff", "--no-edit", "side"], work, check=False).returncode == 0
