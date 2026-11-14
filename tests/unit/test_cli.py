import pytest

from repo_kit import __version__, bootstrap, reconcile
from repo_kit.cli import NOT_IMPLEMENTED, main
from repo_kit.reconcile import Action, Report, Row

COMMANDS = [
    ["check", "commits", "origin/dev..HEAD"],
    ["check", "commit-msg", ".git/COMMIT_EDITMSG"],
    ["check", "pr-title", "Add worktree commands"],
    ["github", "diff"],
    ["github", "apply"],
    ["branches", "prune", "--apply"],
    ["install", "--profile", "dev-main", "--hooks"],
    ["upgrade", "v1.2.3"],
    ["doctor"],
]


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"rk {__version__}"


def test_no_command_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_sync_check_and_adopt_are_exclusive():
    with pytest.raises(SystemExit):
        main(["sync", "--check", "--adopt"])


@pytest.mark.parametrize(
    ("rows", "code"),
    [
        ([], 0),
        ([Row(1, "feat/a", "dev", Action.FAILED, "boom")], 1),
    ],
)
def test_update_prs_exits_non_zero_only_on_a_failure(monkeypatch, capsys, rows, code):
    seen = {}

    def fake_run(_cwd, *, dry_run):
        seen["dry_run"] = dry_run
        return Report(rows, ["did a thing"])

    monkeypatch.setattr(reconcile, "run", fake_run)
    assert main(["update-prs", "--dry-run"]) == code
    assert seen == {"dry_run": True}
    assert "did a thing" in capsys.readouterr().out


@pytest.mark.parametrize("argv", COMMANDS, ids=" ".join)
def test_every_command_parses_and_reports_its_phase(argv, capsys):
    assert main(argv) == NOT_IMPLEMENTED
    assert "not implemented yet (Phase" in capsys.readouterr().err


def test_sync_adopt_is_a_future_item(capsys):
    assert main(["sync", "--adopt"]) == NOT_IMPLEMENTED
    assert "Future item" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "runner"), [(["install"], "run_install"), (["bootstrap"], "run_bootstrap")]
)
def test_install_and_bootstrap_print_what_changed(monkeypatch, capsys, argv, runner):
    seen = {}

    def fake(_cwd, *, profile, changed):
        seen["profile"] = profile
        changed.append("did a thing")

    monkeypatch.setattr(bootstrap, runner, fake)
    assert main(argv) == 0
    assert seen == {"profile": "dev-main"}
    assert "did a thing" in capsys.readouterr().out
