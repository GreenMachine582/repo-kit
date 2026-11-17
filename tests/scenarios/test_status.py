"""`rk status`: the worktrees come from the same code as `wt list`, doctor's result from doctor.
Reads only."""

import json

import pytest
from harness import git

from repo_kit import status
from repo_kit.cli import main
from repo_kit.doctor import Check, Status
from repo_kit.worktree import start, worktree_statuses


@pytest.fixture
def local(clone):
    return clone("local")


@pytest.fixture
def user_env(wt_root, tmp_path, monkeypatch):
    config = tmp_path / "user.toml"
    config.write_text(f"worktree_root = '{wt_root.as_posix()}'\n", encoding="utf-8")
    monkeypatch.setenv("REPO_KIT_CONFIG", str(config))


def test_worktrees_match_wt_list_and_the_pr_store(local, make_ctx, github):
    start(make_ctx(local), "feat/open", no_setup=True)
    github.add_pr("feat/open")
    start(make_ctx(local), "fix/merged", no_setup=True)
    github.add_pr("fix/merged", merged=True)
    start(make_ctx(local), "feat/bare", no_setup=True)

    ctx = make_ctx(local)
    report = status.build(ctx, [Check(Status.OK, "tools", id="tools")])

    assert list(report.worktrees) == worktree_statuses(ctx)[0]
    doc = json.loads(status.to_json(report))
    prs = {w["branch"]: w["pr"] for w in doc["worktrees"]}
    assert prs == {
        "feat/open": {"number": 1, "state": "open"},
        "fix/merged": {"number": 2, "state": "merged"},
        "feat/bare": None,
    }
    assert all(w["pr_known"] for w in doc["worktrees"])
    assert doc["repo"] == "local/origin"
    assert doc["ok"] is True


def test_a_failing_check_is_named_and_makes_it_not_ok(local, make_ctx):
    checks = [
        Check(Status.FAIL, "managed files: 1 drifted", id="files"),
        Check(Status.WARN, "plugin missing", id="plugin"),
    ]
    report = status.build(make_ctx(local), checks)
    text = status.render(report)
    assert "Doctor: 2 checks: 0 OK, 1 WARN, 1 FAIL, 0 SKIP" in text
    assert "[FAIL] managed files: 1 drifted (files)" in text
    assert not report.ok


def test_it_writes_and_fetches_nothing(local, make_ctx):
    start(make_ctx(local), "feat/x", no_setup=True)

    def snap():
        return (
            git(local, "status", "--porcelain", "--untracked-files=all"),
            git(local, "for-each-ref"),
        )

    before = snap()
    status.build(make_ctx(local), [])
    assert snap() == before


def test_cli_json_prints_only_the_document(local, make_ctx, user_env, monkeypatch, capsys):
    start(make_ctx(local), "feat/x", no_setup=True)
    monkeypatch.setattr(status.doctor, "run_checks", lambda cwd: [Check(Status.WARN, "w", id="w")])
    monkeypatch.chdir(local)

    assert main(["status", "--json"]) == 0

    doc = json.loads(capsys.readouterr().out)
    assert doc["schema_version"] == 1
    assert [w["branch"] for w in doc["worktrees"]] == ["feat/x"]
    assert doc["worktrees_note"].startswith("PRs unknown")  # a local bare origin
    assert doc["doctor"]["counts"]["WARN"] == 1


def test_cli_exits_1_only_for_a_fail(local, user_env, monkeypatch, capsys):
    monkeypatch.chdir(local)
    monkeypatch.setattr(status.doctor, "run_checks", lambda cwd: [Check(Status.FAIL, "f", id="f")])
    assert main(["status"]) == 1
    assert "[FAIL] f (f)" in capsys.readouterr().out
    monkeypatch.setattr(status.doctor, "run_checks", lambda cwd: [Check(Status.WARN, "w", id="w")])
    assert main(["status"]) == 0
