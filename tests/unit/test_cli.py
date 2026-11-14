import pytest

from repo_kit import __version__, bootstrap, branches, checks, doctor, github_config, reconcile
from repo_kit.cli import NOT_IMPLEMENTED, main
from repo_kit.reconcile import Action, Report, Row


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


def test_sync_adopt_is_a_future_item(capsys):
    assert main(["sync", "--adopt"]) == NOT_IMPLEMENTED
    assert "Future item" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "runner"), [(["install"], "run_install"), (["bootstrap"], "run_bootstrap")]
)
def test_install_and_bootstrap_print_what_changed(monkeypatch, capsys, argv, runner):
    seen = {}

    def fake(_cwd, *, profile, changed, **_hooks):
        seen["profile"] = profile
        changed.append("did a thing")

    monkeypatch.setattr(bootstrap, runner, fake)
    assert main(argv) == 0
    assert seen == {"profile": "dev-main"}
    assert "did a thing" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "version"), [(["upgrade", "v1.2.3"], "v1.2.3"), (["upgrade"], None)]
)
def test_upgrade_reaches_run_upgrade_and_prints_what_changed(monkeypatch, capsys, argv, version):
    seen = {}

    def fake(_cwd, got, *, changed):
        seen["version"] = got
        changed.append("moved the pin")

    monkeypatch.setattr(bootstrap, "run_upgrade", fake)
    assert main(argv) == 0
    assert seen == {"version": version}
    assert "moved the pin" in capsys.readouterr().out


@pytest.mark.parametrize("code", [0, 1])
def test_doctor_returns_run_doctors_exit_code(monkeypatch, code):
    monkeypatch.setattr(doctor, "run_doctor", lambda _cwd: code)
    assert main(["doctor"]) == code


@pytest.mark.parametrize(
    ("argv", "target", "expected"),
    [
        (["check", "commits", "origin/dev..HEAD"], "run_commits", "origin/dev..HEAD"),
        (["check", "commit-msg", ".git/COMMIT_EDITMSG"], "run_commit_msg", ".git/COMMIT_EDITMSG"),
        (["check", "pr-title", "Add worktree commands"], "run_pr_title", "Add worktree commands"),
    ],
)
def test_check_commands_reach_checks_and_return_its_code(monkeypatch, argv, target, expected):
    seen = []

    def fake(_cwd, arg):
        seen.append(str(arg))
        return 1

    monkeypatch.setattr(checks, target, fake)
    assert main(argv) == 1
    assert [s.replace("\\", "/") for s in seen] == [expected]


@pytest.mark.parametrize(("argv", "hooks"), [(["install"], False), (["install", "--hooks"], True)])
def test_install_passes_hooks_through(monkeypatch, argv, hooks):
    seen = {}

    def fake(_cwd, *, profile, changed, hooks):
        seen["hooks"] = hooks

    monkeypatch.setattr(bootstrap, "run_install", fake)
    assert main(argv) == 0
    assert seen == {"hooks": hooks}


@pytest.mark.parametrize(
    ("argv", "applies"),
    [(["github"], False), (["github", "diff"], False), (["github", "apply"], True)],
)
@pytest.mark.parametrize("code", [0, 1])
def test_github_commands_reach_run_github(monkeypatch, capsys, argv, applies, code):
    seen = {}

    def fake(_cwd, *, apply_changes, changed=None):
        seen["apply"] = apply_changes
        if changed is not None:
            changed.append("created ruleset `repo-kit: tags`")
        return code

    monkeypatch.setattr(github_config, "run_github", fake)
    assert main(argv) == code
    assert seen == {"apply": applies}
    assert ("created ruleset" in capsys.readouterr().out) is applies


@pytest.mark.parametrize(("argv", "applies"), [([], False), (["--apply"], True)])
def test_branches_prune_reaches_run_prune(monkeypatch, capsys, argv, applies):
    seen = {}

    def fake(_cwd, *, apply, changed):
        seen["apply"] = apply
        if apply:
            changed.append("deleted feat/x")
        return 0

    monkeypatch.setattr(branches, "run_prune", fake)
    assert main(["branches", "prune", *argv]) == 0
    assert seen == {"apply": applies}
    assert ("Changed:" in capsys.readouterr().out) is applies
