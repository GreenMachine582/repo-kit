"""A plain `rk sync` pins the generated callers by commit, as `install` and `upgrade` do; `--check`
fails (and never goes to the network) when the lock has no commit for the pin. The remote is a
bare repo in tmp_path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from harness import commit, git

import repo_kit
from repo_kit import fleet
from repo_kit.config import FleetConfig, UserConfig
from repo_kit.files import LOCK_NAME, MANIFEST_NAME, NO_REF_PIN, run_sync
from repo_kit.git import resolve_release_sha

CALLER_PATH = ".github/workflows/pr-checks.yml"
MANIFEST = f'[files."{CALLER_PATH}"]\nmode = "managed"\n'
TOML = 'version = "1.0.0"\n\n[project]\nname = "x"\n'
CALLER = "uses: repo-kit@${ref}${ref_note}\n"


@pytest.fixture
def kit_remote(tmp_path, git_env):
    bare = tmp_path / "kit.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    seed = tmp_path / "kit-seed"
    git(tmp_path, "clone", "-q", str(bare), str(seed))
    sha = commit(seed, "a.txt")
    git(seed, "tag", "v1.0.0")
    git(seed, "push", "-q", "origin", "HEAD", "--tags")
    return bare, sha


@pytest.fixture
def templates(tmp_path):
    root = tmp_path / "templates"
    (root / "common" / ".github/workflows").mkdir(parents=True)
    (root / "common" / MANIFEST_NAME).write_text(MANIFEST, encoding="utf-8")
    (root / "common" / CALLER_PATH).write_text(CALLER, encoding="utf-8")
    return root


@pytest.fixture
def adopted(clone, monkeypatch):
    """A fresh adoption: a pin, and no lock."""
    work = clone("work")
    (work / ".repo-kit.toml").write_text(TOML, encoding="utf-8")
    monkeypatch.setattr(repo_kit, "__version__", "1.0.0")
    return work


def sync(work, templates, *, check=False, resolve=None):
    said: list[str] = []
    code = run_sync(
        work, check=check, changed=[], say=said.append, root=templates, resolve_sha=resolve
    )
    return code, "\n".join(said)


def caller(work: Path) -> str:
    return (work / CALLER_PATH).read_text(encoding="utf-8")


def lock(work: Path) -> dict:
    return json.loads((work / LOCK_NAME).read_text(encoding="utf-8"))


def test_a_fresh_adoption_sync_pins_the_commit_and_then_check_passes(
    adopted, templates, kit_remote
):
    bare, sha = kit_remote
    resolve = lambda v: resolve_release_sha(v, str(bare))  # noqa: E731
    assert sync(adopted, templates, resolve=resolve)[0] == 0
    assert caller(adopted) == f"uses: repo-kit@{sha} # v1.0.0\n"
    assert lock(adopted)["ref_sha"] == sha
    assert lock(adopted)["ref_sha_version"] == "1.0.0"
    # The check reads the lock only: no resolver, and the remote may be gone.
    assert sync(adopted, templates, check=True)[0] == 0


def test_an_existing_ref_sha_is_kept_without_asking_the_remote(adopted, templates, kit_remote):
    bare, sha = kit_remote
    sync(adopted, templates, resolve=lambda v: resolve_release_sha(v, str(bare)))
    before = caller(adopted)

    def forbidden(_version):
        raise AssertionError("the remote must not be asked")

    assert sync(adopted, templates, resolve=forbidden)[0] == 0
    assert caller(adopted) == before
    assert lock(adopted)["ref_sha"] == sha


def test_a_stale_ref_sha_version_is_resolved_again(adopted, templates, kit_remote):
    bare, sha = kit_remote
    resolve = lambda v: resolve_release_sha(v, str(bare))  # noqa: E731
    sync(adopted, templates, resolve=resolve)
    data = lock(adopted)
    data["ref_sha_version"] = "0.9.0"
    data["ref_sha"] = "a" * 40
    (adopted / LOCK_NAME).write_text(json.dumps(data), encoding="utf-8")
    assert sync(adopted, templates, check=True)[0] == 1  # stale: the check says to sync

    assert sync(adopted, templates, resolve=resolve)[0] == 0
    assert lock(adopted)["ref_sha"] == sha
    assert lock(adopted)["ref_sha_version"] == "1.0.0"


def test_check_with_no_ref_sha_fails_clearly_and_goes_offline(adopted, templates):
    # A lock written by an offline sync: the tag only, and nothing else out of date.
    assert sync(adopted, templates)[0] == 0
    assert "ref_sha" not in lock(adopted)
    code, said = sync(adopted, templates, check=True)
    assert code == 1
    assert NO_REF_PIN in said
    assert "rk sync" in said


def test_an_unreachable_remote_warns_and_pins_the_tag_only(adopted, templates, tmp_path):
    resolve = lambda v: resolve_release_sha(v, str(tmp_path / "nowhere.git"))  # noqa: E731
    code, said = sync(adopted, templates, resolve=resolve)
    assert code == 0
    assert "couldn't find the commit of v1.0.0" in said
    assert caller(adopted) == "uses: repo-kit@v1.0.0\n"


def test_unmanaged_callers_need_no_commit(adopted, templates):
    """repo-kit itself: its callers are the workflows, marked `unmanaged`."""
    (adopted / ".repo-kit.toml").write_text(
        TOML + f'\n[overrides.files]\n"{CALLER_PATH}" = "unmanaged"\n', encoding="utf-8"
    )
    assert sync(adopted, templates)[0] == 0
    assert sync(adopted, templates, check=True)[0] == 0


def test_a_repo_without_callers_in_its_templates_needs_no_commit(adopted, tmp_path):
    root = tmp_path / "other"
    (root / "common").mkdir(parents=True)
    (root / "common" / MANIFEST_NAME).write_text('[files."x.txt"]\nmode = "managed"\n')
    (root / "common" / "x.txt").write_text("x\n")
    assert sync(adopted, root)[0] == 0
    assert sync(adopted, root, check=True)[0] == 0


def test_fleet_check_fails_a_repo_with_no_ref_sha(adopted, templates, tmp_path):
    assert sync(adopted, templates)[0] == 0
    user = UserConfig(fleet=FleetConfig({"local/origin": adopted}))
    said: list[str] = []
    assert fleet.run_sync_check(user, templates=templates, say=said.append) == 1
    assert "not pinned by commit" in "\n".join(said)
