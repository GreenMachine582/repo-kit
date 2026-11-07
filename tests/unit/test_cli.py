import pytest

from repo_kit import __version__
from repo_kit.cli import NOT_IMPLEMENTED, main

COMMANDS = [
    ["wt", "start", "feat/x"],
    ["wt", "start", "feat/x", "--from", "feat/base", "--link", "greentechhub-core=feat/y"],
    ["wt", "sync"],
    ["wt", "done", "feat/x", "--allow-unmerged"],
    ["update-prs", "--dry-run"],
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
    ["configure"],
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


@pytest.mark.parametrize("argv", COMMANDS, ids=" ".join)
def test_every_command_parses_and_reports_its_phase(argv, capsys):
    assert main(argv) == NOT_IMPLEMENTED
    assert "not implemented yet (Phase" in capsys.readouterr().err
