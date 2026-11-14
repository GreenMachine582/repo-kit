"""`rk doctor`: scenario 20 (docs/testing.md). A repo fresh from `bootstrap` is all-OK, each
problem is named with the command that fixes it, and nothing is ever written."""

from __future__ import annotations

import json

import pytest
from fakes import FakeRepoSetup
from harness import git

import repo_kit
from repo_kit import bootstrap, doctor, rulesets, settings
from repo_kit.config import PrBotConfig, RepoConfig, UserConfig
from repo_kit.doctor import Status
from repo_kit.files import LOCK_NAME
from repo_kit.worktree import start

KEY = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n"
CALLER = ".github/workflows/update-prs.yml"
OWNER, REPO = "local", "origin"  # what a clone of the bare `origin.git` parses to


@pytest.fixture(autouse=True)
def released_engine(monkeypatch):
    monkeypatch.setattr(repo_kit, "__version__", "1.4.0")
    monkeypatch.setattr(doctor, "installed_gh_version", lambda: (2, 63))


@pytest.fixture
def user_config(tmp_path, wt_root):
    key = tmp_path / "bot.pem"
    key.write_text(KEY, encoding="utf-8")
    return UserConfig(
        worktree_root=wt_root, pr_bot=PrBotConfig(client_id="Iv23abc", private_key=str(key))
    )


@pytest.fixture
def adopted(clone, github, user_config, tmp_path):
    """A clone after `rk bootstrap`, the repo setup it enrolled, and what bootstrap said."""
    work = clone("work")
    setup = FakeRepoSetup()
    # Applied by hand until `rk github apply` exists; the seeded config keeps every ruleset default.
    setup.ruleset_list = [{**r, "id": i} for i, r in enumerate(rulesets.build(RepoConfig()), 1)]
    setup.settings = settings.build(RepoConfig())
    said: list[str] = []
    bootstrap.bootstrap(
        work,
        OWNER,
        REPO,
        github,
        setup,
        user_config,
        source=tmp_path / "config.toml",
        profile="dev-main",
        changed=[],
        confirm=lambda _question: False,
        say=said.append,
    )
    return work, setup, "\n".join(said)


def run(work, github, setup, user_config, tmp_path):
    return doctor.doctor(
        work,
        OWNER,
        REPO,
        github,
        setup,
        user_source=tmp_path / "config.toml",
        user_config=user_config,
    )


def find(checks, text):
    return [c for c in checks if text in c.label]


def snapshot(work, origin):
    return (
        git(work, "status", "--porcelain", "--untracked-files=all"),
        git(work, "for-each-ref"),
        git(origin, "for-each-ref"),
    )


def test_bootstrap_ends_all_ok(adopted, github, user_config, tmp_path):
    work, setup, said = adopted

    checks = run(work, github, setup, user_config, tmp_path)

    assert {c.status for c in checks} <= {Status.OK, Status.SKIP}, doctor.render(checks)
    assert not doctor.failed(checks)
    assert find(checks, "managed files:")[0].label.endswith("drifted")
    assert "[OK]   managed files:" in said
    assert (
        "[OK]   repo settings and rulesets match: repo-kit: dev, repo-kit: main, repo-kit: tags"
        in said
    )
    assert "Fix the [FAIL] items" not in said


def test_doctor_writes_nothing(adopted, origin, github, user_config, tmp_path):
    work, setup, _said = adopted
    (work / CALLER).write_text("edited\n", encoding="utf-8")
    before = snapshot(work, origin)
    writes = list(setup.writes)

    run(work, github, setup, user_config, tmp_path)

    assert snapshot(work, origin) == before
    assert setup.writes == writes


def test_hand_edited_managed_file_fails(adopted, github, user_config, tmp_path):
    work, setup, _said = adopted
    (work / CALLER).write_text("edited\n", encoding="utf-8")

    checks = run(work, github, setup, user_config, tmp_path)

    (files,) = find(checks, "managed files:")
    assert files.status is Status.FAIL
    assert f"{CALLER}: file content was edited by hand" in files.detail
    assert "rk sync --check" in files.detail
    assert doctor.failed(checks)


def test_missing_file_fails(adopted, github, user_config, tmp_path):
    work, setup, _said = adopted
    (work / CALLER).unlink()

    (files,) = find(run(work, github, setup, user_config, tmp_path), "managed files:")

    assert files.status is Status.FAIL
    assert f"{CALLER}: missing" in files.detail


@pytest.mark.parametrize(
    ("unset", "message"),
    [
        (lambda s: s.label_set.pop("needs-merge"), "label needs-merge is missing"),
        (
            lambda s: s.vars.pop(bootstrap.CLIENT_ID_VARIABLE),
            f"variable {bootstrap.CLIENT_ID_VARIABLE} is missing",
        ),
        (
            lambda s: s.vars.update({bootstrap.CLIENT_ID_VARIABLE: "Iv23other"}),
            "isn't the Client ID in your user config",
        ),
        (
            lambda s: s.secrets.pop(bootstrap.PRIVATE_KEY_SECRET),
            f"secret {bootstrap.PRIVATE_KEY_SECRET} is missing",
        ),
    ],
)
def test_enrolment_gap_fails_and_names_configure(
    adopted, github, user_config, tmp_path, unset, message
):
    work, setup, _said = adopted
    unset(setup)

    (bot,) = find(run(work, github, setup, user_config, tmp_path), "gth-pr-bot not enrolled")

    assert bot.status is Status.FAIL
    assert message in bot.detail
    assert "rk configure" in bot.detail


def test_override_and_orphan_warn(adopted, github, user_config, tmp_path):
    work, setup, _said = adopted
    config = work / ".repo-kit.toml"
    config.write_text(
        config.read_text(encoding="utf-8") + f'\n[overrides.files]\n"{CALLER}" = "unmanaged"\n',
        encoding="utf-8",
    )
    lock = json.loads((work / LOCK_NAME).read_text(encoding="utf-8"))
    lock["files"]["old/gone.txt"] = {"layer": "common", "mode": "managed", "sha256": "x"}
    (work / LOCK_NAME).write_text(json.dumps(lock), encoding="utf-8")

    checks = run(work, github, setup, user_config, tmp_path)

    (override,) = find(checks, "override in use")
    assert override.status is Status.WARN
    assert override.label == f"override in use: {CALLER} is unmanaged"
    (orphan,) = find(checks, "not a template: old/gone.txt")
    assert orphan.status is Status.WARN


def test_merged_worktree_is_stale(adopted, github, user_config, tmp_path, make_ctx):
    work, setup, _said = adopted
    path = start(make_ctx(work), "feat/x", no_setup=True)
    github.add_pr("feat/x", merged=True)
    start(make_ctx(work), "feat/y", no_setup=True)
    github.add_pr("feat/y")

    (stale,) = find(run(work, github, setup, user_config, tmp_path), "stale worktree")

    assert stale.status is Status.WARN
    assert stale.label == "1 stale worktree"
    assert f"{path} (feat/x: PR #1 merged)" in stale.detail
    assert "rk wt done <branch>" in stale.detail


def test_pin_from_another_release_warns_and_skips_files(
    adopted, github, user_config, tmp_path, monkeypatch
):
    work, setup, _said = adopted
    monkeypatch.setattr(repo_kit, "__version__", "1.5.0")

    checks = run(work, github, setup, user_config, tmp_path)

    (pin,) = find(checks, ".repo-kit.toml valid")
    assert pin.status is Status.WARN
    assert "rk upgrade" in pin.detail
    (files,) = find(checks, "managed files")
    assert files.status is Status.SKIP


def test_invalid_config_fails_and_skips_what_needs_it(adopted, github, user_config, tmp_path):
    work, setup, _said = adopted
    config = work / ".repo-kit.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\nunknown = 1\n", encoding="utf-8")

    checks = run(work, github, setup, user_config, tmp_path)

    (bad,) = find(checks, ".repo-kit.toml invalid")
    assert bad.status is Status.FAIL
    assert "unknown" in bad.detail
    (skipped,) = find(checks, "managed files, plugin, base branch, gth-pr-bot")
    assert skipped.status is Status.SKIP


def test_fresh_clone_fails_and_points_to_install(clone, github, user_config, tmp_path):
    work = clone("work")

    checks = run(work, github, FakeRepoSetup(), user_config, tmp_path)

    (missing,) = find(checks, ".repo-kit.toml missing")
    assert missing.status is Status.FAIL
    assert "rk install" in missing.detail


def test_missing_base_branch_fails(adopted, origin, github, user_config, tmp_path):
    work, setup, _said = adopted
    git(work, "update-ref", "-d", "refs/remotes/origin/dev")

    (base,) = find(run(work, github, setup, user_config, tmp_path), "base branch")

    assert base.status is Status.FAIL
    assert "rk install" in base.detail


def test_ruleset_drift_is_a_warning_naming_the_change(adopted, github, user_config, tmp_path):
    work, setup, _said = adopted
    setup.ruleset_list[2]["bypass_actors"].append(
        {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
    )
    del setup.ruleset_list[1]
    setup.settings["allow_squash_merge"] = True

    [check] = find(run(work, github, setup, user_config, tmp_path), "rulesets")

    assert check.status is Status.WARN
    assert check.label == "repo settings or rulesets differ from .repo-kit.toml"
    assert "repo settings: drifted\n  allow_squash_merge:\n    - true\n    + false" in check.detail
    assert "repo-kit: main: missing (would be created)" in check.detail
    assert "    - Integration 5058502 always, RepositoryRole 5 always" in check.detail
    assert "`rk github diff`, then `rk github apply`" in check.detail


def test_unreadable_rulesets_are_a_warning(adopted, github, user_config, tmp_path, monkeypatch):
    work, setup, _said = adopted

    def refuse():
        raise doctor.RkError("HTTP 403")

    monkeypatch.setattr(setup, "rulesets", refuse)
    [check] = find(run(work, github, setup, user_config, tmp_path), "rulesets")
    assert (check.status, check.label, check.detail) == (
        Status.WARN,
        "repo settings and rulesets: can't read them",
        "HTTP 403",
    )
