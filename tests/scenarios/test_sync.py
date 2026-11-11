"""`rk sync` scenarios 14-17 and 19 (docs/testing.md), in a real clone with test templates."""

from __future__ import annotations

import json

import pytest
from harness import git

from repo_kit.files import LOCK_NAME, MANIFEST_NAME, Action, SyncError, run_sync

MANIFEST = """
[files."ci/caller.yml"]
mode = "managed"

[files.".gitignore"]
mode = "block"
block = "local"
source = "gitignore.block"

[files."CONTRIBUTING.md"]
mode = "block"
block = "branches"
source = "branches.md"

[files."CHANGELOG.md"]
mode = "seeded"
source = "changelog.md"
"""

TEMPLATES = {
    "ci/caller.yml": "uses: repo-kit@v${version}\nwith: ${{ github.sha }}\n",
    "gitignore.block": ".repo-kit.local.toml\n",
    "branches.md": "Branch off `${default_base}` (v${version}).\n",
    "changelog.md": "# Changelog for ${project_name}\n",
}


@pytest.fixture
def templates(tmp_path):
    root = tmp_path / "templates"
    common = root / "common"
    common.mkdir(parents=True)
    (common / MANIFEST_NAME).write_text(MANIFEST, encoding="utf-8")
    for name, content in TEMPLATES.items():
        (common / name).parent.mkdir(parents=True, exist_ok=True)
        (common / name).write_text(content, encoding="utf-8")
    return root


@pytest.fixture
def repo(clone):
    work = clone("work")
    set_version(work, "1.0.0")
    return work


def set_version(work, version, extra=""):
    (work / ".repo-kit.toml").write_text(f'version = "{version}"\n{extra}', encoding="utf-8")


def sync(work, templates, check=False):
    changed: list[str] = []
    said: list[str] = []
    code = run_sync(work, check=check, changed=changed, say=said.append, root=templates)
    return code, changed, "\n".join(said)


def read(work, path):
    return (work / path).read_text(encoding="utf-8")


def test_a_first_sync_writes_every_file_and_the_lock(repo, templates):
    code, changed, said = sync(repo, templates)
    assert code == 0
    assert read(repo, "ci/caller.yml") == "uses: repo-kit@v1.0.0\nwith: ${{ github.sha }}\n"
    assert "# >>> repo-kit: local\n.repo-kit.local.toml\n# <<< repo-kit: local" in read(
        repo, ".gitignore"
    )
    assert "<!-- >>> repo-kit: branches -->\nBranch off `dev` (v1.0.0)." in read(
        repo, "CONTRIBUTING.md"
    )
    # The repo name comes from the origin URL, which is the bare `origin.git` here.
    assert read(repo, "CHANGELOG.md") == "# Changelog for origin\n"
    lock = json.loads(read(repo, LOCK_NAME))
    assert lock["version"] == "1.0.0"
    assert set(lock["files"]) == {"ci/caller.yml", ".gitignore", "CONTRIBUTING.md", "CHANGELOG.md"}
    assert f"wrote {LOCK_NAME}" in changed
    assert sync(repo, templates, check=True)[0] == 0


def test_scenario_14_a_second_sync_changes_nothing(repo, templates):
    sync(repo, templates)
    code, changed, said = sync(repo, templates)
    assert code == 0
    assert changed == []
    assert said.count(str(Action.UNCHANGED)) == 4


def test_scenario_15_a_hand_edited_managed_file_is_refused(repo, templates):
    sync(repo, templates)
    (repo / "ci/caller.yml").write_text("uses: something-else\n", encoding="utf-8")
    set_version(repo, "1.1.0")
    lock_before = read(repo, LOCK_NAME)

    with pytest.raises(SyncError, match="refused ci/caller.yml: nothing was written"):
        sync(repo, templates)

    assert read(repo, "ci/caller.yml") == "uses: something-else\n"
    # All or nothing: the block that would have updated wasn't written either.
    assert "(v1.0.0)" in read(repo, "CONTRIBUTING.md")
    assert read(repo, LOCK_NAME) == lock_before
    code, _changed, said = sync(repo, templates, check=True)
    assert code == 1
    assert "-uses: something-else" in said
    assert "+uses: repo-kit@v1.1.0" in said


def test_scenario_16_text_outside_the_markers_is_kept(repo, templates):
    (repo / "CONTRIBUTING.md").write_text("# Contributing\n\nOur own intro.\n", encoding="utf-8")
    _code, first, _said = sync(repo, templates)
    assert "updated CONTRIBUTING.md" in first  # an existing file gains a block: updated, not created
    text = read(repo, "CONTRIBUTING.md") + "\n## Our own section\n"
    (repo / "CONTRIBUTING.md").write_text(text, encoding="utf-8")
    set_version(repo, "1.1.0")

    code, changed, _said = sync(repo, templates)

    after = read(repo, "CONTRIBUTING.md")
    assert after.startswith("# Contributing\n\nOur own intro.\n")
    assert after.endswith("## Our own section\n")
    assert "(v1.1.0)" in after
    assert "updated CONTRIBUTING.md" in changed


def test_a_hand_edited_block_is_refused(repo, templates):
    sync(repo, templates)
    text = read(repo, ".gitignore").replace(".repo-kit.local.toml", "something")
    (repo / ".gitignore").write_text(text, encoding="utf-8")
    with pytest.raises(SyncError, match="refused .gitignore"):
        sync(repo, templates)


def test_scenario_17_a_seeded_file_is_never_overwritten(repo, templates):
    sync(repo, templates)
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## 1.0.0\n- shipped\n", encoding="utf-8")
    (templates / "common" / "changelog.md").write_text("# New stub\n", encoding="utf-8")

    code, changed, _said = sync(repo, templates)

    assert read(repo, "CHANGELOG.md") == "# Changelog\n\n## 1.0.0\n- shipped\n"
    assert changed == []
    assert sync(repo, templates, check=True)[0] == 0


def test_scenario_19_an_identical_existing_file_is_taken_over(repo, templates):
    (repo / "ci").mkdir()
    (repo / "ci/caller.yml").write_text(
        "uses: repo-kit@v1.0.0\nwith: ${{ github.sha }}\n", encoding="utf-8"
    )
    code, changed, said = sync(repo, templates)
    assert f"{Action.ADOPT}  ci/caller.yml" in said
    assert "ci/caller.yml" in json.loads(read(repo, LOCK_NAME))["files"]


def test_scenario_19_a_differing_existing_file_is_refused_and_nothing_written(repo, templates):
    (repo / "ci").mkdir()
    (repo / "ci/caller.yml").write_text("uses: our-own\n", encoding="utf-8")

    with pytest.raises(SyncError, match="refused ci/caller.yml"):
        sync(repo, templates)

    assert not (repo / LOCK_NAME).exists()
    assert not (repo / "CHANGELOG.md").exists()
    assert read(repo, "ci/caller.yml") == "uses: our-own\n"


def test_crlf_endings_are_not_an_edit(repo, templates):
    sync(repo, templates)
    path = repo / "ci/caller.yml"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert sync(repo, templates)[1] == []
    assert sync(repo, templates, check=True)[0] == 0


def test_an_unmanaged_override_is_skipped(repo, templates):
    set_version(repo, "1.0.0", '[overrides]\nfiles."ci/caller.yml" = "unmanaged"\n')
    (repo / "ci").mkdir()
    (repo / "ci/caller.yml").write_text("ours\n", encoding="utf-8")
    code, _changed, said = sync(repo, templates)
    assert code == 0
    assert read(repo, "ci/caller.yml") == "ours\n"
    assert "skipped  ci/caller.yml (unmanaged)" in said


def test_a_replaced_file_is_backed_up_outside_the_tree(repo, templates):
    sync(repo, templates)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "chore: sync")
    set_version(repo, "1.1.0")

    _code, changed, _said = sync(repo, templates)

    [backup_line] = [line for line in changed if line.startswith("backed up")]
    backup_dir = backup_line.removeprefix("backed up the replaced files to ")
    from pathlib import Path

    backup = Path(backup_dir) / "ci/caller.yml"
    assert backup.read_text(encoding="utf-8") == "uses: repo-kit@v1.0.0\nwith: ${{ github.sha }}\n"
    assert ".git" in backup.parts
    status = git(repo, "status", "--porcelain").splitlines()
    assert sorted(line.split(maxsplit=1)[1] for line in status) == sorted(
        [".repo-kit.lock", ".repo-kit.toml", "CONTRIBUTING.md", "ci/caller.yml"]
    )


def test_a_template_dropped_from_the_manifest_is_reported_and_left(repo, templates):
    sync(repo, templates)
    manifest = templates / "common" / MANIFEST_NAME
    manifest.write_text(
        MANIFEST.replace('[files."ci/caller.yml"]\nmode = "managed"\n', ""), "utf-8"
    )
    code, changed, said = sync(repo, templates)
    assert "not a template  ci/caller.yml" in said
    assert (repo / "ci/caller.yml").exists()


def test_a_missing_lock_is_drift(repo, templates):
    sync(repo, templates)
    (repo / LOCK_NAME).unlink()
    assert sync(repo, templates, check=True)[0] == 1


def test_an_empty_version_is_refused(repo, templates):
    set_version(repo, "")
    with pytest.raises(SyncError, match="`version` is empty"):
        sync(repo, templates)
