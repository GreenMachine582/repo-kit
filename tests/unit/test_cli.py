import pytest

from repo_kit import __version__, reconcile
from repo_kit.cli import NOT_IMPLEMENTED, main
from repo_kit.reconcile import Action, Report, Row

COMMANDS = [
    ["check", "commits", "origin/dev..HEAD"],
    ["check", "commit-msg", ".git/COMMIT_EDITMSG"],
    ["check", "pr-title", "Add worktree commands"],
    ["sync"],
    ["sync", "--check"],
    ["sync", "--adopt"],
    ["github", "diff"],
    ["github", "apply"],
    ["branches", "prune", "--apply"],
    ["install", "--profile", "dev-main", "--hooks"],
    ["bootstrap"],
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
