"""The update-prs rules, with GitHub's answers scripted."""

from __future__ import annotations

import pytest
from fakes import FakeGitHub

from repo_kit import reconcile as reconcile_module
from repo_kit.config import ConfigError, RepoConfig
from repo_kit.errors import RkError
from repo_kit.github import BaseStatus, GitHubError, PullRequest, UpdateResult
from repo_kit.reconcile import (
    Action,
    Report,
    Row,
    load_config,
    parents_first,
    reconcile,
    render_summary,
    run,
    skip_reason,
)


class Scripted(FakeGitHub):
    """Answers statuses from a script and records updates, with no Git behind it."""

    def __init__(self, statuses: list[BaseStatus] | None = None) -> None:
        super().__init__()
        self.statuses = list(statuses or [])
        self.updates: list[tuple[int, str]] = []

    def list_open_prs(self) -> list[PullRequest]:
        return [pr for pr in self.prs if pr.state == "open"]

    def base_status(self, pr: PullRequest) -> BaseStatus:
        return self.statuses.pop(0)

    def update_branch(self, number: int, expected_head_sha: str) -> UpdateResult:
        self.updates.append((number, expected_head_sha))
        return UpdateResult(ok=True)


def pr(number: int, head: str = "feat/a", base: str = "dev", **kwargs: object) -> PullRequest:
    return PullRequest(number, "open", False, False, base, head, **kwargs)


def go(github: FakeGitHub, **kwargs: object) -> Report:
    sleeps: list[float] = []
    report = reconcile(github, RepoConfig(), sleep=sleeps.append, **kwargs)
    report.sleeps = sleeps  # type: ignore[attr-defined]
    return report


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"draft": True}, "draft"),
        ({"from_fork": True}, "from a fork"),
        ({"labels": ("wip",)}, "labelled wip"),
        ({"labels": ("no-auto-update",)}, "labelled no-auto-update"),
        ({"head_ref": "release-please--branches--main"}, "release-please branch"),
        ({"head_ref": "dev", "base_ref": "main"}, "head is the long-lived branch dev"),
        ({"state": "closed"}, "no longer open"),
    ],
)
def test_skip_reasons(kwargs, reason):
    fields = {"state": "open", "draft": False, "base_ref": "dev", "head_ref": "feat/a", **kwargs}
    assert skip_reason(PullRequest(1, merged=False, **fields), RepoConfig()) == reason


def test_an_ordinary_pr_is_eligible():
    assert skip_reason(pr(1), RepoConfig()) is None


def test_parents_first_orders_a_chain_opened_backwards():
    chain = [pr(1, "c", "b"), pr(2, "b", "a"), pr(3, "a", "dev")]
    assert [p.head_ref for p in parents_first(chain)] == ["a", "b", "c"]


def test_parents_first_is_stable_for_unrelated_prs():
    assert [p.number for p in parents_first([pr(3, "z"), pr(1, "x"), pr(2, "y")])] == [1, 2, 3]


def test_parents_first_survives_a_cycle():
    cycle = [pr(1, "a", "b"), pr(2, "b", "a")]
    assert {p.number for p in parents_first(cycle)} == {1, 2}


def test_unknown_is_retried_until_github_answers():
    github = Scripted([BaseStatus.UNKNOWN, BaseStatus.UNKNOWN, BaseStatus.BEHIND])
    github.add_pr("feat/a")
    report = go(github)
    assert report.rows[0].action is Action.UPDATED
    assert report.sleeps == [3, 3]


def test_unknown_gives_up_and_changes_nothing():
    github = Scripted([BaseStatus.UNKNOWN] * 4)
    github.add_pr("feat/a")
    report = go(github)
    assert report.rows[0].action is Action.UNKNOWN
    assert github.updates == []
    assert report.changed == []


def test_a_refused_update_is_recorded_and_keeps_the_label():
    github = Scripted([BaseStatus.BEHIND])
    github.update_branch = lambda _number, _sha: UpdateResult(False, "HTTP 422")
    github.add_pr("feat/a", labels=("needs-merge",))
    report = go(github)
    assert report.rows[0] == Row(1, "feat/a", "dev", Action.REJECTED, "HTTP 422")
    assert github.removed_labels == []


@pytest.mark.parametrize("status", [BaseStatus.CURRENT, BaseStatus.BEHIND])
def test_the_conflict_label_clears_once_resolved(status):
    github = Scripted([status])
    github.add_pr("feat/a", labels=("needs-merge",))
    go(github)
    assert github.removed_labels == [(1, "needs-merge")]


def test_a_label_added_after_the_list_is_honoured():
    github = Scripted()
    github.list_open_prs = lambda: [pr(1)]
    github.add_pr("feat/a", labels=("wip",))
    report = go(github)
    assert report.rows[0].detail == "labelled wip"
    assert github.updates == []


def test_the_live_base_is_used_after_a_retarget():
    github = Scripted([BaseStatus.BEHIND])
    github.list_open_prs = lambda: [pr(1, base="feat/old")]
    github.add_pr("feat/a", base="dev")
    assert go(github).rows[0].base == "dev"


def test_the_update_is_guarded_by_the_listed_sha():
    github = Scripted([BaseStatus.BEHIND])
    github.list_open_prs = lambda: [pr(1, head_sha="listed")]
    github.add_pr("feat/a")
    go(github)
    assert github.updates == [(1, "listed")]


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (BaseStatus.BEHIND, Action.UPDATED),
        (BaseStatus.CONFLICTING, Action.CONFLICT),
        (BaseStatus.CURRENT, Action.UP_TO_DATE),
    ],
)
def test_a_dry_run_writes_nothing(status, action):
    github = Scripted([status])
    github.add_pr("feat/a", labels=("needs-merge",) if status is BaseStatus.CURRENT else ())
    report = go(github, dry_run=True)
    assert report.rows[0].action is action
    assert report.changed == []
    assert github.updates == []
    assert github.added_labels == []
    assert github.removed_labels == []
    assert github.comments == {}


def test_one_failure_does_not_stop_the_rest():
    github = Scripted([BaseStatus.BEHIND, BaseStatus.BEHIND])
    calls = []

    def update(number, _sha):
        calls.append(number)
        if number == 1:
            raise GitHubError("boom")
        return UpdateResult(True)

    github.update_branch = update
    github.add_pr("feat/a")
    github.add_pr("feat/b")
    report = go(github)
    assert calls == [1, 2]
    assert report.failed
    assert [row.action for row in report.rows] == [Action.FAILED, Action.UPDATED]


def test_summary_is_one_row_per_pr_and_escapes_cells():
    report = Report(
        [
            Row(2, "feat/b", "dev", Action.FAILED, "line one\nline | two"),
            Row(1, "a", "dev", Action.UPDATED),
        ]
    )
    lines = render_summary(report).splitlines()
    assert lines[0].startswith("| PR |")
    assert lines[2].startswith("| #1 |")
    assert lines[3] == "| #2 | feat/b | dev | failed | line one line \\| two |"


def test_a_missing_config_means_the_defaults():
    assert load_config(FakeGitHub()) == RepoConfig()


def test_the_config_is_read_from_the_api():
    github = FakeGitHub()
    github.files[".repo-kit.toml"] = '[update_prs]\nconflict_label = "clash"\n'
    assert load_config(github).update_prs.conflict_label == "clash"


def test_bad_config_text_is_a_config_error():
    github = FakeGitHub()
    github.files[".repo-kit.toml"] = "not toml ="
    with pytest.raises(ConfigError, match=".repo-kit.toml"):
        load_config(github)


def test_disabled_does_nothing(tmp_path, capsys):
    github = Scripted()
    github.files[".repo-kit.toml"] = "[update_prs]\nenabled = false\n"
    github.add_pr("feat/a")
    assert run(tmp_path, github=github).rows == []
    assert "disabled" in capsys.readouterr().out


def test_run_prints_and_appends_the_job_summary(tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    summary.write_text("earlier\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(reconcile_module.time, "sleep", lambda _seconds: None)
    github = Scripted([BaseStatus.CURRENT])
    github.add_pr("feat/a")
    run(tmp_path, github=github)
    written = summary.read_text(encoding="utf-8")
    assert written.startswith("earlier\n| PR |")
    assert "| #1 | feat/a |" in capsys.readouterr().out


def test_repo_comes_from_the_actions_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    assert reconcile_module._resolve_repo(tmp_path) == ("o", "r")


def test_a_malformed_actions_repo_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "nope")
    with pytest.raises(RkError, match="owner/name"):
        reconcile_module._resolve_repo(tmp_path)


def test_repo_falls_back_to_origin(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(reconcile_module.git, "remote_url", lambda _c: "git@github.com:o/r.git")
    assert reconcile_module._resolve_repo(tmp_path) == ("o", "r")


def test_a_non_github_origin_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(reconcile_module.git, "remote_url", lambda _c: "/srv/origin.git")
    with pytest.raises(RkError, match="isn't on GitHub"):
        reconcile_module._resolve_repo(tmp_path)
