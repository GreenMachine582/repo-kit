"""`rk upgrade` scenario 18 (docs/testing.md), in a real clone with test templates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import repo_kit
from repo_kit.bootstrap import upgrade
from repo_kit.errors import RkError
from repo_kit.files import LOCK_NAME, MANIFEST_NAME, run_sync

MANIFEST = """
[files."ci/caller.yml"]
mode = "managed"
"""

MANIFEST_NEW = (
    MANIFEST
    + """
[files."ci/new.yml"]
mode = "managed"
"""
)

TOML = '# my pin\nversion = "1.0.0"  # keep me\n\n[project]\nname = "x"\n'


@pytest.fixture
def templates(tmp_path):
    root = tmp_path / "templates"
    common = root / "common"
    (common / "ci").mkdir(parents=True)
    (common / MANIFEST_NAME).write_text(MANIFEST, encoding="utf-8")
    (common / "ci/caller.yml").write_text("uses: repo-kit@v${version}\n", encoding="utf-8")
    (common / "ci/new.yml").write_text("new at v${version}\n", encoding="utf-8")
    return root


def add_new_template(templates):
    (templates / "common" / MANIFEST_NAME).write_text(MANIFEST_NEW, encoding="utf-8")


def remove_caller_template(templates):
    (templates / "common" / MANIFEST_NAME).write_text(
        '[files."ci/new.yml"]\nmode = "managed"\n', encoding="utf-8"
    )


@pytest.fixture
def repo(clone, templates, monkeypatch):
    """A repo synced at 1.0.0, with the engine then moved to 1.1.0."""
    work = clone("work")
    (work / ".repo-kit.toml").write_bytes(TOML.encode())
    monkeypatch.setattr(repo_kit, "__version__", "1.0.0")
    assert run_sync(work, check=False, changed=[], say=lambda _: None, root=templates) == 0
    monkeypatch.setattr(repo_kit, "__version__", "1.1.0")
    return work


def run(work, templates, target, changed=None):
    said: list[str] = []
    upgrade(
        work,
        "o",
        "origin",
        target,
        changed=[] if changed is None else changed,
        say=said.append,
        templates=templates,
    )
    return "\n".join(said)


def snapshot(work):
    return {
        p.relative_to(work).as_posix(): p.read_bytes()
        for p in work.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(work).parts
    }


def test_scenario_18_an_upgrade_that_adds_a_template_adds_it_and_updates_the_lock(repo, templates):
    add_new_template(templates)
    run(repo, templates, "v1.1.0")
    assert (repo / "ci/new.yml").read_text(encoding="utf-8") == "new at v1.1.0\n"
    assert (repo / "ci/caller.yml").read_text(encoding="utf-8") == "uses: repo-kit@v1.1.0\n"
    lock = json.loads((repo / LOCK_NAME).read_text(encoding="utf-8"))
    assert lock["version"] == "1.1.0"
    assert "ci/new.yml" in lock["files"]
    assert (repo / ".repo-kit.toml").read_text(encoding="utf-8") == TOML.replace("1.0.0", "1.1.0")
    code = run_sync(repo, check=True, changed=[], say=lambda _: None, root=templates)
    assert code == 0


def test_scenario_18_a_hand_edited_managed_file_stops_the_upgrade_before_any_write(repo, templates):
    add_new_template(templates)
    (repo / "ci/caller.yml").write_text("edited\n", encoding="utf-8")
    before = snapshot(repo)
    with pytest.raises(RkError, match="refused"):
        run(repo, templates, "1.1.0")
    assert snapshot(repo) == before


def test_scenario_18_a_target_other_than_the_engine_is_refused(repo, templates):
    before = snapshot(repo)
    with pytest.raises(RkError, match=r"1\.2\.0.*1\.1\.0.*Install rk 1\.2\.0"):
        run(repo, templates, "v1.2.0")
    assert snapshot(repo) == before


def test_scenario_18_a_malformed_target_is_refused(repo, templates):
    with pytest.raises(RkError, match="vX.Y.Z"):
        run(repo, templates, "latest")


def test_scenario_18_a_second_upgrade_changes_nothing(repo, templates):
    add_new_template(templates)
    run(repo, templates, "v1.1.0")
    changed: list[str] = []
    said = run(repo, templates, "v1.1.0", changed)
    assert "already at 1.1.0" in said
    assert changed == []


def test_scenario_18_no_argument_upgrades_to_the_running_engine(repo, templates):
    run(repo, templates, None)
    assert 'version = "1.1.0"' in (repo / ".repo-kit.toml").read_text(encoding="utf-8")


def test_scenario_18_unreleased_engine_needs_an_explicit_version(repo, templates, monkeypatch):
    monkeypatch.setattr(repo_kit, "__version__", "0.0.0")
    with pytest.raises(RkError, match="explicitly"):
        run(repo, templates, None)
    run(repo, templates, "1.3.0")
    assert 'version = "1.3.0"' in (repo / ".repo-kit.toml").read_text(encoding="utf-8")


def test_scenario_18_a_downgrade_is_refused(repo, templates, monkeypatch):
    monkeypatch.setattr(repo_kit, "__version__", "0.9.0")
    before = snapshot(repo)
    with pytest.raises(RkError, match="downgrade"):
        run(repo, templates, "0.9.0")
    assert snapshot(repo) == before


def test_scenario_18_the_old_config_is_backed_up_outside_the_tree(repo, templates):
    changed: list[str] = []
    run(repo, templates, "1.1.0", changed)
    entry = next(c for c in changed if c.startswith("backed up the old .repo-kit.toml to "))
    backup = entry.removeprefix("backed up the old .repo-kit.toml to ")
    assert ".git" in backup
    assert Path(backup).read_text(encoding="utf-8") == TOML


def test_scenario_18_a_template_removed_in_the_new_release_is_reported_and_left(repo, templates):
    remove_caller_template(templates)
    said = run(repo, templates, "1.1.0")
    assert "ci/caller.yml" in said
    assert (repo / "ci/caller.yml").is_file()
    lock = json.loads((repo / LOCK_NAME).read_text(encoding="utf-8"))
    assert "ci/caller.yml" in lock["files"]
