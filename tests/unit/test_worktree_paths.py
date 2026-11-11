import hashlib
import os
from pathlib import Path

import pytest

from repo_kit.errors import RkError
from repo_kit.worktree import command_args, hash6, parse_links, slug, validate_branch, worktree_path


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("feat/api-v2", "feat-api-v2"),
        ("feat/api/v2", "feat-api-v2"),
        ("fix/scan_resume.2", "fix-scan_resume.2"),
        ("feat/a//b", "feat-a-b"),
        ("feat/" + "x" * 60, ("feat-" + "x" * 60)[:40]),
        ("feat/" + "a" * 34 + "-bcd", "feat-" + "a" * 34),  # no trailing '-' after the cut
    ],
)
def test_slug(branch, expected):
    assert slug(branch) == expected
    assert len(slug(branch)) <= 40


def test_hash6_is_sha1_prefix():
    assert (
        hash6("feat/api-v2") == hashlib.sha1(b"feat/api-v2", usedforsecurity=False).hexdigest()[:6]
    )


def test_colliding_slugs_get_distinct_paths():
    root = Path("/wt")
    a = worktree_path(root, "GreenMachine582", "photo-ledger", "feat/api-v2")
    b = worktree_path(root, "GreenMachine582", "photo-ledger", "feat/api/v2")
    assert a.parent == b.parent == root / "GreenMachine582-photo-ledger"
    assert a.name.startswith("feat-api-v2-") and b.name.startswith("feat-api-v2-")
    assert a != b


@pytest.mark.parametrize(
    "name", ["feat/api-v2", "fix/scan-resume", "docs/adoption", "feat/api/v2", "ci/Windows_Job.2"]
)
def test_valid_branch_names(name):
    validate_branch(name)


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("api-v2", "<type>/<description>"),
        ("feat/", "<type>/<description>"),
        ("feat", "<type>/<description>"),
        ("feature/x", "invalid branch type 'feature'"),
        ("Feat/x", "<type>/<description>"),
        ("feat/a b", "<type>/<description>"),
        ("feat/-x", "<type>/<description>"),
        ("feat/x/", "<type>/<description>"),
        ("feat/a..b", "Git doesn't allow"),
        ("feat/x.lock", "Git doesn't allow"),
        ("feat/x.", "Git doesn't allow"),
    ],
)
def test_invalid_branch_names(name, reason):
    with pytest.raises(RkError, match=reason.replace(".", r"\.")):
        validate_branch(name)


def test_parse_links():
    assert parse_links(["core=feat/x", "api=fix/y"]) == {"core": "feat/x", "api": "fix/y"}
    for bad in (["core"], ["=feat/x"], ["core="]):
        with pytest.raises(RkError, match="invalid --link"):
            parse_links(bad)
    with pytest.raises(RkError, match="given twice"):
        parse_links(["core=feat/x", "core=feat/y"])


def test_command_args_runs_shell_scripts_through_bash():
    args = command_args("./scripts/check.sh --fast")
    assert args[1:] == ["./scripts/check.sh", "--fast"]
    assert Path(args[0]).stem == "bash"
    if os.name == "nt":  # Git Bash, never System32's WSL launcher
        assert "system32" not in args[0].lower()
    assert command_args('uv pip install -e ".[dev]"')[-1] == ".[dev]"
    with pytest.raises(RkError, match="empty command"):
        command_args("  ")
