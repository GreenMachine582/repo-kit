"""Callers pin repo-kit by commit as well as tag: resolved by `rk upgrade`, recorded in the lock,
rendered offline by `rk sync`, and checked against the tag by `rk doctor`. The remote is a bare
repo in tmp_path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from harness import commit, git

import repo_kit
from repo_kit import doctor
from repo_kit.bootstrap import upgrade
from repo_kit.config import RepoConfig
from repo_kit.doctor import Status
from repo_kit.errors import RkError
from repo_kit.files import LOCK_NAME, MANIFEST_NAME, lock_ref_sha, run_sync
from repo_kit.git import is_sha, resolve_release_sha

MANIFEST = '[files."ci/caller.yml"]\nmode = "managed"\n'
TOML = 'version = "1.0.0"\n\n[project]\nname = "x"\n'
CALLER = "uses: repo-kit@${ref}${ref_note}\nwith: ref=${ref}\n"


@pytest.fixture
def kit_remote(tmp_path):
    """A stand-in for repo-kit's remote: v1.0.0 lightweight, v1.1.0 annotated."""
    bare = tmp_path / "kit.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    seed = tmp_path / "kit-seed"
    git(tmp_path, "clone", "-q", str(bare), str(seed))
    first = commit(seed, "a.txt")
    git(seed, "tag", "v1.0.0")
    second = commit(seed, "b.txt")
    git(seed, "tag", "-a", "v1.1.0", "-m", "release 1.1.0")
    git(seed, "push", "-q", "origin", "HEAD", "--tags")
    return bare, {"1.0.0": first, "1.1.0": second}, seed


def test_resolve_release_sha_follows_lightweight_and_annotated_tags(kit_remote):
    bare, shas, _ = kit_remote
    assert resolve_release_sha("1.0.0", str(bare)) == shas["1.0.0"]
    assert (
        resolve_release_sha("1.1.0", str(bare)) == shas["1.1.0"]
    )  # the commit, not the tag object
    assert is_sha(shas["1.1.0"])


def test_resolve_release_sha_says_when_the_tag_or_remote_is_missing(kit_remote, tmp_path):
    bare, _, _ = kit_remote
    with pytest.raises(RkError, match="no tag v9.9.9"):
        resolve_release_sha("9.9.9", str(bare))
    with pytest.raises(RkError, match="can't reach"):
        resolve_release_sha("1.0.0", str(tmp_path / "nowhere.git"))


@pytest.fixture
def templates(tmp_path):
    root = tmp_path / "templates"
    (root / "common" / "ci").mkdir(parents=True)
    (root / "common" / MANIFEST_NAME).write_text(MANIFEST, encoding="utf-8")
    (root / "common/ci/caller.yml").write_text(CALLER, encoding="utf-8")
    return root


@pytest.fixture
def repo(clone, templates, monkeypatch):
    work = clone("work")
    (work / ".repo-kit.toml").write_text(TOML, encoding="utf-8")
    monkeypatch.setattr(repo_kit, "__version__", "1.0.0")
    assert run_sync(work, check=False, changed=[], say=lambda _: None, root=templates) == 0
    monkeypatch.setattr(repo_kit, "__version__", "1.1.0")
    return work


def caller(work: Path) -> str:
    return (work / "ci/caller.yml").read_text(encoding="utf-8")


def lock(work: Path) -> dict:
    return json.loads((work / LOCK_NAME).read_text(encoding="utf-8"))


def do_upgrade(work, templates, resolve, target="1.1.0"):
    said: list[str] = []
    upgrade(
        work, "o", "origin", target, changed=[], say=said.append, templates=templates,
        resolve_sha=resolve,
    )  # fmt: skip
    return "\n".join(said)


def test_a_sync_without_a_recorded_commit_pins_the_tag_only(repo):
    assert caller(repo) == "uses: repo-kit@v1.0.0\nwith: ref=v1.0.0\n"
    assert "ref_sha" not in lock(repo)


def test_upgrade_records_the_commit_and_renders_it_with_the_tag_as_a_comment(
    repo, templates, kit_remote
):
    bare, shas, _ = kit_remote
    do_upgrade(repo, templates, lambda v: resolve_release_sha(v, str(bare)))
    sha = shas["1.1.0"]
    assert caller(repo) == f"uses: repo-kit@{sha} # v1.1.0\nwith: ref={sha}\n"
    assert lock(repo)["ref_sha"] == sha
    assert lock(repo)["ref_sha_version"] == "1.1.0"


def test_a_later_sync_renders_the_recorded_commit_without_the_network(repo, templates, kit_remote):
    bare, shas, _ = kit_remote
    do_upgrade(repo, templates, lambda v: resolve_release_sha(v, str(bare)))
    before = caller(repo)
    # No resolver, and the remote gone: sync only reads the lock.
    assert run_sync(repo, check=True, changed=[], say=lambda _: None, root=templates) == 0
    assert caller(repo) == before


def test_a_hand_edited_sha_fails_sync_check(repo, templates, kit_remote):
    bare, _, _ = kit_remote
    do_upgrade(repo, templates, lambda v: resolve_release_sha(v, str(bare)))
    path = repo / "ci/caller.yml"
    path.write_text(path.read_text(encoding="utf-8").replace("uses: repo-kit@", "uses: repo-kit@0"))
    assert run_sync(repo, check=True, changed=[], say=lambda _: None, root=templates) != 0


def test_upgrade_refuses_a_tag_that_moved(repo, templates, kit_remote):
    bare, shas, seed = kit_remote
    resolve = lambda v: resolve_release_sha(v, str(bare))  # noqa: E731
    do_upgrade(repo, templates, resolve)
    recorded = caller(repo)
    git(seed, "tag", "-f", "-a", "v1.1.0", "-m", "moved", shas["1.0.0"])
    git(seed, "push", "-q", "-f", "origin", "v1.1.0")
    with pytest.raises(RkError, match="a release tag must not move"):
        do_upgrade(repo, templates, resolve)
    assert caller(repo) == recorded


def test_upgrade_that_cant_resolve_warns_and_pins_the_tag_only(repo, templates):
    def offline(_version):
        raise RkError("no network")

    said = do_upgrade(repo, templates, offline)
    assert "callers pin the tag only" in said
    assert caller(repo) == "uses: repo-kit@v1.1.0\nwith: ref=v1.1.0\n"
    assert "ref_sha" not in lock(repo)


def test_a_commit_recorded_for_another_release_is_ignored_but_kept(repo, templates, kit_remote):
    """What a release PR leaves: the lock's version moved, the commit still the old release's."""
    bare, shas, _ = kit_remote
    do_upgrade(repo, templates, lambda v: resolve_release_sha(v, str(bare)))
    data = lock(repo)
    data["version"] = "1.2.0"  # release-please moves this and the pin, nothing else
    (repo / LOCK_NAME).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    (repo / ".repo-kit.toml").write_text(TOML.replace("1.0.0", "1.2.0"), encoding="utf-8")
    assert lock_ref_sha(data, "1.2.0") == ""
    monkey = pytest.MonkeyPatch()
    monkey.setattr(repo_kit, "__version__", "1.2.0")
    try:
        assert run_sync(repo, check=False, changed=[], say=lambda _: None, root=templates) == 0
    finally:
        monkey.undo()
    assert caller(repo) == "uses: repo-kit@v1.2.0\nwith: ref=v1.2.0\n"
    assert lock(repo)["ref_sha"] == shas["1.1.0"]  # carried, so the release PR's lock is stable


# --- doctor ----------------------------------------------------------------------------------


def recorded_lock(tmp_path: Path, sha: str | None, version: str = "1.0.0") -> RepoConfig:
    data: dict = {"version": version, "files": {}}
    if sha:
        data |= {"ref_sha": sha, "ref_sha_version": version}
    (tmp_path / LOCK_NAME).write_text(json.dumps(data), encoding="utf-8")
    return RepoConfig(version=version)


SHA = "a" * 40


def test_doctor_warns_when_the_callers_pin_only_a_tag(tmp_path):
    check = doctor.check_ref_pin(tmp_path, recorded_lock(tmp_path, None), None)
    assert check is not None and check.status is Status.WARN
    assert "rk upgrade" in check.detail


def test_doctor_checks_the_commit_against_the_tag(tmp_path):
    config = recorded_lock(tmp_path, SHA)
    ok = doctor.check_ref_pin(tmp_path, config, lambda _v: SHA)
    assert ok is not None and ok.status is Status.OK
    bad = doctor.check_ref_pin(tmp_path, config, lambda _v: "b" * 40)
    assert bad is not None and bad.status is Status.FAIL
    assert "must not move" in bad.detail


def test_doctor_only_warns_when_the_remote_cant_be_reached(tmp_path):
    def offline(_v):
        raise RkError("no network")

    check = doctor.check_ref_pin(tmp_path, recorded_lock(tmp_path, SHA), offline)
    assert check is not None and check.status is Status.WARN
    skipped = doctor.check_ref_pin(tmp_path, recorded_lock(tmp_path, SHA), None)
    assert skipped is not None and skipped.status is Status.SKIP


def test_doctor_skips_a_repo_whose_callers_are_unmanaged(tmp_path):
    from repo_kit.config import OverridesConfig
    from repo_kit.files import CALLERS

    config = RepoConfig(
        version="1.0.0", overrides=OverridesConfig(files={p: "unmanaged" for p in CALLERS})
    )
    assert doctor.check_ref_pin(tmp_path, config, lambda _v: SHA) is None
