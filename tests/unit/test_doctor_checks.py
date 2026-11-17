"""`rk doctor`'s pure parts: the gh version, the tools line, the report format, the exit rule."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from repo_kit import doctor, git
from repo_kit.credentials import APPS, AppCredentials
from repo_kit.doctor import Check, Status
from repo_kit.errors import RkError


@pytest.mark.parametrize(
    ("text", "version"),
    [
        (
            "gh version 2.63.0 (2024-11-27)\nhttps://github.com/cli/cli/releases/tag/v2.63.0\n",
            (2, 63),
        ),
        ("gh version 2.40.1 (2023-12-13)", (2, 40)),
    ],
)
def test_parse_gh_version(text, version):
    assert doctor.parse_gh_version(text) == version


def test_parse_gh_version_refuses_other_text():
    with pytest.raises(RkError):
        doctor.parse_gh_version("hub version 2.14")


@pytest.fixture
def git_247(monkeypatch):
    monkeypatch.setattr(git, "version", lambda _cwd: (2, 47))


def test_tools_ok(git_247):
    check = doctor.check_tools(Path(), lambda: (2, 63), lambda: (3, 10))
    assert check.status is Status.OK
    assert check.label.startswith("git 2.47 / gh 2.63 / sops 3.10 / Python 3.")


def test_tools_missing_gh_warns(git_247):
    check = doctor.check_tools(Path(), lambda: None, lambda: (3, 10))
    assert check.status is Status.WARN
    assert "gh missing" in check.label
    assert "gh auth login" in check.detail


def test_tools_old_gh_fails(git_247):
    check = doctor.check_tools(Path(), lambda: (2, 39), lambda: (3, 10))
    assert check.status is Status.FAIL
    assert "gh 2.40 or newer" in check.detail


def test_tools_old_git_fails(monkeypatch):
    monkeypatch.setattr(git, "version", lambda _cwd: (2, 37))
    check = doctor.check_tools(Path(), lambda: (2, 63), lambda: (3, 10))
    assert check.status is Status.FAIL
    assert "Git 2.38 or newer" in check.detail


def test_render_aligns_status_and_indents_detail():
    text = doctor.render(
        [
            Check(Status.OK, "managed files: 9 in sync, 0 drifted"),
            Check(Status.WARN, "1 stale worktree", "a (feat/x: PR #1 merged)\nrk wt done"),
            Check(Status.FAIL, "gth-pr-bot not enrolled"),
            Check(Status.SKIP, "repo settings and rulesets"),
        ]
    )
    assert text.splitlines() == [
        "[OK]   managed files: 9 in sync, 0 drifted",
        "[WARN] 1 stale worktree",
        "       a (feat/x: PR #1 merged)",
        "       rk wt done",
        "[FAIL] gth-pr-bot not enrolled",
        "[SKIP] repo settings and rulesets",
    ]


def test_only_a_fail_fails():
    assert not doctor.failed(
        [Check(Status.OK, "a"), Check(Status.WARN, "b"), Check(Status.SKIP, "c")]
    )
    assert doctor.failed([Check(Status.OK, "a"), Check(Status.FAIL, "b")])


def test_tools_missing_or_old_sops_warns(git_247):
    missing = doctor.check_tools(Path(), lambda: (2, 63), lambda: None)
    assert missing.status is Status.WARN
    assert "sops missing" in missing.label
    old = doctor.check_tools(Path(), lambda: (2, 63), lambda: (3, 8))
    assert old.status is Status.WARN
    assert "sops 3.10+" in old.detail


def test_to_json_is_one_stable_document():
    import json

    checks = [
        Check(Status.OK, "a", id="ia"),
        Check(Status.WARN, "b", "x\ny", "ib"),
        Check(Status.SKIP, "c", id="ic"),
    ]
    doc = json.loads(doctor.to_json(checks))
    assert doc == {
        "schema_version": 1,
        "ok": True,
        "checks": [
            {"id": "ia", "name": "a", "status": "OK", "detail": ""},
            {"id": "ib", "name": "b", "status": "WARN", "detail": "x\ny"},
            {"id": "ic", "name": "c", "status": "SKIP", "detail": ""},
        ],
    }
    assert json.loads(doctor.to_json([*checks, Check(Status.FAIL, "d")]))["ok"] is False


def test_run_doctor_json_says_only_json(monkeypatch, tmp_path):
    import json

    monkeypatch.setattr(git, "toplevel", lambda cwd: tmp_path)
    monkeypatch.setattr(git, "remote_url", lambda root: "https://example.com/o/r.git")
    monkeypatch.setattr(doctor, "user_config_path", lambda: tmp_path / "none.toml")
    monkeypatch.setattr(doctor, "doctor", lambda *a, **k: [Check(Status.FAIL, "x", "d")])
    said: list[str] = []
    assert doctor.run_doctor(tmp_path, as_json=True, say=said.append) == 1
    assert len(said) == 1
    assert json.loads(said[0])["ok"] is False


def test_error_json_has_no_checks():
    import json

    doc = json.loads(doctor.error_json("not a git repository"))
    assert doc == {
        "schema_version": 1,
        "ok": False,
        "error": "not a git repository",
        "checks": [],
    }


def test_run_doctor_json_reports_an_early_error(monkeypatch, tmp_path):
    import json

    def boom(cwd):
        raise RkError("not a git repository")

    monkeypatch.setattr(git, "toplevel", boom)
    said: list[str] = []
    assert doctor.run_doctor(tmp_path, as_json=True, say=said.append) == 1
    assert json.loads(said[0])["error"] == "not a git repository"
    with pytest.raises(RkError):
        doctor.run_doctor(tmp_path, say=said.append)


TODAY = date(2026, 6, 1)


def _creds(pr: str, release: str) -> dict[str, AppCredentials]:
    return {
        "pr_bot": AppCredentials("c1", "k", pr),
        "release_bot": AppCredentials("c2", "k", release),
    }


def test_key_age_ok_when_both_are_recent():
    check = doctor.check_key_age(_creds("2026-05-01", "2026-01-01"), APPS, 365, TODAY)
    assert check.status is Status.OK
    assert check.id == "key-age"


def test_key_age_warns_past_the_limit():
    check = doctor.check_key_age(_creds("2026-05-01", "2025-05-31"), APPS, 365, TODAY)
    assert check.status is Status.WARN
    assert "gth-release-bot's key was stored 366 days ago" in check.detail
    assert "gth-pr-bot" not in check.detail


def test_key_age_exactly_at_the_limit_is_fine():
    assert (
        doctor.check_key_age(_creds("2025-06-01", "2025-06-01"), APPS, 365, TODAY).status
        is Status.OK
    )


def test_key_age_without_a_date_skips_never_fails():
    check = doctor.check_key_age(_creds("", "2026-05-01"), APPS, 365, TODAY)
    assert check.status is Status.SKIP
    assert "gth-pr-bot" in check.label
    assert doctor.check_key_age(_creds("not-a-date", ""), APPS, 365, TODAY).status is Status.SKIP


def test_key_age_a_warn_beats_a_missing_date():
    check = doctor.check_key_age(_creds("", "2020-01-01"), APPS, 365, TODAY)
    assert check.status is Status.WARN


def test_key_age_skips_without_credentials():
    assert doctor.check_key_age(None, APPS, 365, TODAY).status is Status.SKIP
