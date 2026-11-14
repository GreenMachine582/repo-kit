"""`rk github diff` and `rk github apply` against the fake repo setup, in a real clone (the default
branch is only set once it exists on origin)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import FakeRepoSetup
from harness import git

from repo_kit import github_config, rulesets, settings
from repo_kit.config import RepoConfig
from repo_kit.errors import RkError

CONFIG = RepoConfig()
OTHER = {"id": 7, "name": "release tags", "target": "tag", "rules": [{"type": "creation"}]}


@pytest.fixture(autouse=True)
def not_ci(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


@pytest.fixture
def fresh() -> FakeRepoSetup:
    """A repo before rk: GTH's settings on `main`, and one ruleset rk doesn't own."""
    setup = FakeRepoSetup()
    setup.settings = {**settings.build(CONFIG), "default_branch": "main", "has_wiki": True}
    setup.ruleset_list = [dict(OTHER)]
    return setup


def matching() -> FakeRepoSetup:
    setup = FakeRepoSetup()
    setup.settings = settings.build(CONFIG)
    setup.ruleset_list = [{**r, "id": i} for i, r in enumerate(rulesets.build(CONFIG), 1)]
    return setup


def apply(work, setup, answer=True):
    asked: list[str] = []
    said: list[str] = []
    changed: list[str] = []

    def confirm(question):
        asked.append(question)
        return answer

    code = github_config.apply(
        work, CONFIG, setup, target="o/r", confirm=confirm, changed=changed, say=said.append
    )
    return code, asked, changed, "\n".join(said)


def test_diff_reports_settings_then_rulesets(fresh):
    said: list[str] = []
    assert github_config.run_diff(CONFIG, fresh, say=said.append) == 1
    assert said[:4] == [
        "repo settings: drifted",
        "  default_branch:",
        '    - "main"',
        '    + "dev"',
    ]
    assert "repo-kit: tags: missing (would be created)" in said
    assert "Left alone (not rk's): release tags" in said
    assert said[-1].startswith("GitHub differs from .repo-kit.toml")


def test_diff_of_a_matching_repo_is_0():
    said: list[str] = []
    assert github_config.run_diff(CONFIG, matching(), say=said.append) == 0
    assert said[-1] == "GitHub matches .repo-kit.toml."


def test_apply_to_a_fresh_repo(clone, fresh):
    work = clone("work")
    code, asked, changed, said = apply(work, fresh)

    assert code == 0
    assert asked == ["Apply these changes to o/r?"]
    assert fresh.writes == [
        "settings default_branch",
        "ruleset create repo-kit: dev",
        "ruleset create repo-kit: main",
        "ruleset create repo-kit: tags",
    ]
    assert fresh.settings["default_branch"] == "dev"
    assert OTHER in fresh.ruleset_list  # never touched
    assert changed[:4] == [
        "updated repo settings: default_branch",
        "created ruleset `repo-kit: dev`",
        "created ruleset `repo-kit: main`",
        "created ruleset `repo-kit: tags`",
    ]
    assert said.endswith("GitHub matches .repo-kit.toml.")

    [backup_line] = [c for c in changed if c.startswith("backed up")]
    backup = json.loads(Path(backup_line.split(" to ", 1)[1]).read_text(encoding="utf-8"))
    assert backup["settings"]["default_branch"] == "main"
    assert backup["rulesets"] == [OTHER]


def test_a_rerun_writes_nothing(clone, fresh):
    work = clone("work")
    apply(work, fresh)
    writes = list(fresh.writes)

    code, asked, changed, said = apply(work, fresh)

    assert (code, asked, changed) == (0, [], [])
    assert fresh.writes == writes
    assert said.endswith("Nothing to apply: GitHub matches .repo-kit.toml.")


def test_a_drifted_ruleset_is_updated_in_place(clone):
    setup = matching()
    setup.ruleset_list[0]["bypass_actors"] = []
    code, _asked, changed, _said = apply(clone("work"), setup)

    assert code == 0
    assert setup.writes == ["ruleset update repo-kit: dev"]
    assert [r["id"] for r in setup.ruleset_list] == [1, 2, 3]
    assert changed[0] == "updated ruleset `repo-kit: dev`"


def test_declining_writes_nothing(clone, fresh):
    work = clone("work")
    code, asked, changed, said = apply(work, fresh, answer=False)
    assert (code, changed, fresh.writes) == (1, [], [])
    assert len(asked) == 1
    assert said.endswith("Nothing applied.")
    assert not (work / ".git" / "repo-kit").exists()  # no backup either


def test_apply_refuses_in_ci(clone, fresh, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(RkError, match="never runs in CI"):
        apply(clone("work"), fresh)
    assert fresh.writes == []


def test_a_default_branch_missing_on_origin_is_left_out(clone, fresh, origin):
    work = clone("work")
    git(work, "push", "-q", "origin", "--delete", "dev")
    git(work, "fetch", "-q", "--prune")

    code, _asked, changed, said = apply(work, fresh)

    assert "`dev` isn't on origin yet, so the default branch is left as it is" in said
    assert "settings default_branch" not in fresh.writes
    assert fresh.settings["default_branch"] == "main"
    assert "created ruleset `repo-kit: dev`" in changed
    assert code == 1  # still differs: the default branch
    assert "Still different after applying:" in said
