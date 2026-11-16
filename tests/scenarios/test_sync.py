"""`rk sync` scenarios 14-17 and 19 (docs/testing.md), in a real clone with test templates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from harness import git

import repo_kit
from repo_kit.files import LOCK_NAME, MANIFEST_NAME, Action, SyncError, run_sync

MANIFEST = """
[files."ci/caller.yml"]
mode = "managed"

[files.".gitignore"]
mode = "block"
blocks = { local = "gitignore.block" }

[files."CONTRIBUTING.md"]
mode = "block"
blocks = { branches = "branches.md", releasing = "releasing.md" }

[files."CHANGELOG.md"]
mode = "seeded"
source = "changelog.md"
"""

TEMPLATES = {
    "ci/caller.yml": "uses: repo-kit@v${version}\nwith: ${{ github.sha }}\n",
    "gitignore.block": ".repo-kit.local.toml\n",
    "branches.md": "Branch off `${default_base}` (v${version}).\n",
    # No final newline, and the same at every version.
    "releasing.md": "Releases are cut from `main`.",
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


@pytest.fixture(autouse=True)
def unreleased_engine(monkeypatch):
    """These scenarios move the pin freely, which only an unreleased engine allows."""
    monkeypatch.setattr(repo_kit, "__version__", "0.0.0")


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
    # An existing file that gains a block is updated, not created.
    assert "updated CONTRIBUTING.md" in first
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


def test_two_blocks_in_one_file_are_appended_in_order(repo, templates):
    sync(repo, templates)
    assert read(repo, "CONTRIBUTING.md") == (
        "<!-- >>> repo-kit: branches -->\nBranch off `dev` (v1.0.0).\n"
        "<!-- <<< repo-kit: branches -->\n\n"
        "<!-- >>> repo-kit: releasing -->\nReleases are cut from `main`.\n"
        "<!-- <<< repo-kit: releasing -->\n"
    )
    blocks = json.loads(read(repo, LOCK_NAME))["files"]["CONTRIBUTING.md"]["blocks"]
    assert sorted(blocks) == ["branches", "releasing"]
    assert blocks["branches"]["layer"] == "common"


def test_one_block_updates_while_the_others_and_the_text_between_stay(repo, templates):
    sync(repo, templates)
    text = read(repo, "CONTRIBUTING.md").replace(
        "\n\n<!-- >>> repo-kit: releasing", "\n\n## Ours\n\n<!-- >>> repo-kit: releasing"
    )
    (repo / "CONTRIBUTING.md").write_text(text, encoding="utf-8")
    set_version(repo, "1.1.0")

    _code, _changed, said = sync(repo, templates)

    assert "CONTRIBUTING.md (branches block updated)" in said
    assert read(repo, "CONTRIBUTING.md") == text.replace("(v1.0.0)", "(v1.1.0)")


def test_one_edited_block_refuses_the_whole_file(repo, templates):
    sync(repo, templates)
    text = read(repo, "CONTRIBUTING.md").replace("cut from `main`", "cut from `trunk`")
    (repo / "CONTRIBUTING.md").write_text(text, encoding="utf-8")
    set_version(repo, "1.1.0")

    with pytest.raises(SyncError, match="refused CONTRIBUTING.md"):
        sync(repo, templates)

    # The branches block would have updated, but a file is written whole or not at all.
    assert read(repo, "CONTRIBUTING.md") == text
    _code, _changed, said = sync(repo, templates, check=True)
    assert "releasing block: block content was edited by hand" in said


def test_a_block_template_without_a_final_newline_stays_in_sync(repo, templates):
    sync(repo, templates)
    assert sync(repo, templates)[1] == []
    assert sync(repo, templates, check=True)[0] == 0


def test_a_block_added_to_the_manifest_later_joins_the_file(repo, templates):
    manifest = templates / "common" / MANIFEST_NAME
    both = 'blocks = { branches = "branches.md", releasing = "releasing.md" }'
    manifest.write_text(MANIFEST.replace(both, 'blocks = { branches = "branches.md" }'), "utf-8")
    sync(repo, templates)
    manifest.write_text(MANIFEST, "utf-8")

    _code, changed, said = sync(repo, templates)

    assert "CONTRIBUTING.md (releasing block added)" in said
    assert "updated CONTRIBUTING.md" in changed
    assert read(repo, "CONTRIBUTING.md").count(">>> repo-kit:") == 2


def test_check_shows_the_diff_of_an_out_of_date_file(repo, templates):
    sync(repo, templates)
    set_version(repo, "1.1.0")
    code, _changed, said = sync(repo, templates, check=True)
    assert code == 1
    assert "-uses: repo-kit@v1.0.0" in said
    assert "+uses: repo-kit@v1.1.0" in said
    assert "-Branch off `dev` (v1.0.0)." in said


def test_a_pin_that_differs_from_a_released_engine_is_refused(repo, templates, monkeypatch):
    monkeypatch.setattr(repo_kit, "__version__", "1.2.0")
    with pytest.raises(SyncError, match=r"pins repo-kit 1\.0\.0, but this rk is 1\.2\.0"):
        sync(repo, templates)
    assert not (repo / LOCK_NAME).exists()
    monkeypatch.setattr(repo_kit, "__version__", "1.0.0")
    assert sync(repo, templates)[0] == 0


def test_a_target_that_is_a_directory_is_refused(repo, templates):
    (repo / "ci/caller.yml").mkdir(parents=True)
    with pytest.raises(SyncError, match="refused ci/caller.yml"):
        sync(repo, templates)
    assert "ci/caller.yml (is a directory)" in sync(repo, templates, check=True)[2]


def test_a_target_that_is_not_text_is_refused(repo, templates):
    (repo / "ci").mkdir()
    (repo / "ci/caller.yml").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(SyncError, match="refused ci/caller.yml"):
        sync(repo, templates)


def test_a_symlink_never_takes_a_write_outside_the_repo(repo, templates, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (repo / "ci").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this machine doesn't allow creating symlinks")

    with pytest.raises(SyncError, match="refused ci/caller.yml"):
        sync(repo, templates)

    assert list(outside.iterdir()) == []
    assert "outside the repo" in sync(repo, templates, check=True)[2]


def test_the_release_manifest_is_seeded_from_the_project_version(tmp_path, repo):
    root = tmp_path / "manifest-templates"
    (root / "common").mkdir(parents=True)
    (root / "common" / MANIFEST_NAME).write_text(
        '[files.".release-please-manifest.json"]\nmode = "seeded"\nsource = "m.json"\n',
        encoding="utf-8",
    )
    (root / "common" / "m.json").write_text('{\n  ".": "${current_version}"\n}\n', encoding="utf-8")
    pyproject = repo / "pyproject.toml"
    pyproject.write_text('[project]\nname = "demo"\nversion = "0.5.0"\n', encoding="utf-8")

    assert sync(repo, root)[0] == 0
    assert json.loads(read(repo, ".release-please-manifest.json")) == {".": "0.5.0"}

    # Seeded: once it exists it's release-please's, whatever the project file says next.
    pyproject.write_text('[project]\nname = "demo"\nversion = "0.6.0"\n', encoding="utf-8")
    code, changed, _ = sync(repo, root)
    assert code == 0
    assert ".release-please-manifest.json" not in changed
    assert json.loads(read(repo, ".release-please-manifest.json")) == {".": "0.5.0"}
