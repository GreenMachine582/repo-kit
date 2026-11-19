"""`rk doctor`: scenario 20 (docs/testing.md). A repo fresh from `bootstrap` is all-OK, each
problem is named with the command that fixes it, and nothing is ever written."""

from __future__ import annotations

import json
import re

import pytest
from fakes import NEW_PEM, PEM, RELEASE_PEM, FakeRepoSetup, stored_credentials
from harness import git

import repo_kit
from repo_kit import bootstrap, doctor, rulesets, settings
from repo_kit.config import RepoConfig, UserConfig
from repo_kit.credentials import (
    PR_BOT,
    RELEASE_BOT,
    key_fingerprint,
    load_credentials,
    rotate_key,
)
from repo_kit.doctor import Status
from repo_kit.files import LOCK_NAME
from repo_kit.worktree import start

CALLER = ".github/workflows/update-prs.yml"
OWNER, REPO = "local", "origin"  # what a clone of the bare `origin.git` parses to


@pytest.fixture(autouse=True)
def released_engine(monkeypatch):
    monkeypatch.setattr(repo_kit, "__version__", "1.4.0")
    monkeypatch.setattr(doctor, "installed_gh_version", lambda: (2, 63))


@pytest.fixture
def user_config(tmp_path, wt_root):
    path, _sops = stored_credentials(tmp_path)
    return UserConfig(worktree_root=wt_root, secrets_file=str(path))


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
        stored_credentials(tmp_path)[1],
        source=tmp_path / "config.toml",
        profile="dev-main",
        changed=[],
        confirm=lambda _question: False,
        say=said.append,
        resolve_sha=lambda _version: "a" * 40,
    )
    return work, setup, "\n".join(said)


def run(work, github, setup, user_config, tmp_path, sops=None):
    return doctor.doctor(
        work,
        OWNER,
        REPO,
        github,
        setup,
        user_source=tmp_path / "config.toml",
        user_config=user_config,
        sops=sops or stored_credentials(tmp_path)[1],
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
    # A machine set up to sign, as the dev and main rulesets require. Set only now: the test's git
    # has no signing key, so earlier commits couldn't have been signed.
    git(work, "config", "commit.gpgsign", "true")

    checks = run(work, github, setup, user_config, tmp_path)

    assert {c.status for c in checks} <= {Status.OK, Status.SKIP}, doctor.render(checks)
    assert not doctor.failed(checks)
    ids = [c.id for c in checks]
    assert all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", i) for i in ids), ids
    assert len(ids) == len(set(ids)), ids
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
            lambda s: s.vars.pop(PR_BOT.client_id_variable),
            f"variable {PR_BOT.client_id_variable} is missing",
        ),
        (
            lambda s: s.vars.update({PR_BOT.client_id_variable: "Iv23other"}),
            "isn't the Client ID in your credentials file",
        ),
        (
            lambda s: s.env_secrets[PR_BOT.environment].pop(PR_BOT.private_key_secret),
            f"secret {PR_BOT.private_key_secret} is missing from environment {PR_BOT.environment}",
        ),
        (
            lambda s: s.environments.pop(PR_BOT.environment),
            f"environment {PR_BOT.environment} is missing",
        ),
        (
            lambda s: s.environments.update({PR_BOT.environment: ["*"]}),
            f"environment {PR_BOT.environment} allows *, not only branch dev",
        ),
        (
            # An rk before environments: the key is readable from any pushed branch.
            lambda s: s.secrets.update({PR_BOT.private_key_secret: PEM}),
            f"secret {PR_BOT.private_key_secret} is a repo-level secret, which a workflow on any "
            "pushed branch can read",
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


def test_release_bot_gap_fails_on_its_own(adopted, github, user_config, tmp_path):
    work, setup, _said = adopted
    setup.env_secrets[RELEASE_BOT.environment].pop(RELEASE_BOT.private_key_secret)

    checks = run(work, github, setup, user_config, tmp_path)

    (bot,) = find(checks, "gth-release-bot not enrolled")
    assert f"secret {RELEASE_BOT.private_key_secret} is missing" in bot.detail
    assert find(checks, "gth-pr-bot:")[0].status is Status.OK


def test_an_app_the_repo_doesnt_use_may_be_missing(adopted, github, user_config, tmp_path):
    work, setup, _said = adopted
    config = work / ".repo-kit.toml"
    config.write_text(
        config.read_text(encoding="utf-8") + "\n[update_prs]\nenabled = false\n", encoding="utf-8"
    )
    path, sops = stored_credentials(tmp_path)
    del sops.files[path]["pr_bot"]

    checks = run(work, github, setup, user_config, tmp_path, sops=sops)

    (creds,) = find(checks, "credentials file")
    assert (creds.status, creds.label) == (Status.OK, "credentials file: gth-release-bot")
    assert find(checks, "gth-pr-bot: update-prs is disabled")[0].status is Status.SKIP
    assert find(checks, "gth-release-bot:")[0].status is Status.OK


def test_an_unreadable_credentials_file_warns_and_skips_only_the_comparison(
    adopted, github, user_config, tmp_path
):
    work, setup, _said = adopted
    _path, sops = stored_credentials(tmp_path)
    sops.readable = False
    setup.vars[PR_BOT.client_id_variable] = "Iv23other"  # can't be compared, so not a FAIL
    setup.vars[PR_BOT.key_fingerprint_variable] = "0" * 64  # nor can the key

    checks = run(work, github, setup, user_config, tmp_path, sops=sops)

    (creds,) = find(checks, "credentials file")
    assert creds.status is Status.WARN
    assert "no matching age key" in creds.detail
    assert not doctor.failed(checks), doctor.render(checks)


def test_bootstrap_records_each_keys_fingerprint(adopted, github, user_config, tmp_path):
    work, setup, _said = adopted
    assert setup.vars[PR_BOT.key_fingerprint_variable] == key_fingerprint(PEM)
    assert setup.vars[RELEASE_BOT.key_fingerprint_variable] == key_fingerprint(RELEASE_PEM)

    checks = run(work, github, setup, user_config, tmp_path)

    assert find(checks, "gth-pr-bot:")[0].label.endswith("key fingerprint matches")


def test_a_rotated_key_fails_until_configure_replaces_it(adopted, github, user_config, tmp_path):
    work, setup, _said = adopted
    path, sops = stored_credentials(tmp_path)
    sops.files[path]["release_bot"]["private_key"] = NEW_PEM  # rotated in the credentials file

    checks = run(work, github, setup, user_config, tmp_path, sops=sops)

    (bot,) = find(checks, "gth-release-bot: older key")
    assert bot.status is Status.FAIL
    assert f"secret {RELEASE_BOT.private_key_secret} holds an older key" in bot.detail
    assert "run `rk configure --replace-key`" in bot.detail
    assert find(checks, "gth-pr-bot:")[0].status is Status.OK

    credentials = load_credentials(user_config, tmp_path / "config.toml", sops)
    bootstrap.configure(
        setup, RepoConfig(), credentials, changed=[], replace_key=True, say=lambda _m: None
    )
    checks = run(work, github, setup, user_config, tmp_path, sops=sops)
    assert find(checks, "gth-release-bot:")[0].status is Status.OK


def test_a_secret_without_a_fingerprint_warns(adopted, github, user_config, tmp_path):
    work, setup, _said = adopted
    setup.vars.pop(PR_BOT.key_fingerprint_variable)  # set by an rk before fingerprints

    checks = run(work, github, setup, user_config, tmp_path)

    (bot,) = find(checks, "gth-pr-bot: key unknown")
    assert bot.status is Status.WARN
    assert f"variable {PR_BOT.key_fingerprint_variable} is missing" in bot.detail
    assert "rk configure --replace-key" in bot.detail
    assert not doctor.failed(checks), doctor.render(checks)


def test_a_onedrive_conflict_copy_warns(adopted, github, user_config, tmp_path):
    work, setup, _said = adopted
    copy = tmp_path / "secrets.sops-DESKTOP-AB12.json"
    copy.write_text("{}", encoding="utf-8")

    checks = run(work, github, setup, user_config, tmp_path)

    (creds,) = find(checks, "credentials file")
    assert creds.status is Status.WARN
    assert copy.name in creds.detail
    assert "merge the two" in creds.detail
    assert find(checks, "gth-pr-bot:")[0].status is Status.OK  # the file still decrypted
    assert copy.is_file()  # only named, never touched


def test_missing_sops_is_a_warning(adopted, github, user_config, tmp_path):
    work, setup, _said = adopted
    _path, sops = stored_credentials(tmp_path)
    sops.installed = None

    tools = run(work, github, setup, user_config, tmp_path, sops=sops)[0]

    assert tools.status is Status.WARN
    assert "sops missing" in tools.label


def test_override_and_orphan_warn(adopted, github, user_config, tmp_path):
    work, setup, _said = adopted
    config = work / ".repo-kit.toml"
    overrides = f'\n[overrides.files]\n"{CALLER}" = "unmanaged"\n"old/gone.txt" = "unmanaged"\n'
    config.write_text(config.read_text(encoding="utf-8") + overrides, encoding="utf-8")
    # A stale lock entry is removed by sync; only an unmanaged one stays as a warning.
    lock = json.loads((work / LOCK_NAME).read_text(encoding="utf-8"))
    lock["files"]["old/gone.txt"] = {"layer": "common", "mode": "managed", "sha256": "x"}
    (work / LOCK_NAME).write_text(json.dumps(lock), encoding="utf-8")

    checks = run(work, github, setup, user_config, tmp_path)

    (override,) = find(checks, f"override in use: {CALLER}")
    assert override.status is Status.WARN
    assert override.label == f"override in use: {CALLER} is unmanaged"
    (orphan,) = find(checks, "not a template: old/gone.txt")
    assert orphan.status is Status.WARN


def test_an_override_on_a_reusable_workflow_is_ok(adopted, github, user_config, tmp_path):
    # repo-kit's case: the caller's path holds the reusable workflow itself.
    work, setup, _said = adopted
    (work / CALLER).write_text(
        "name: update-prs\n\non:\n  workflow_call:\n    inputs: {}\n", encoding="utf-8"
    )
    config = work / ".repo-kit.toml"
    config.write_text(
        config.read_text(encoding="utf-8") + f'\n[overrides.files]\n"{CALLER}" = "unmanaged"\n',
        encoding="utf-8",
    )

    checks = run(work, github, setup, user_config, tmp_path)

    assert find(checks, "override in use") == []
    (override,) = find(checks, f"override: {CALLER}")
    assert override.status is Status.OK
    assert "reusable workflow" in override.detail


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


def test_pin_ahead_of_this_rk_says_to_install_the_newer_rk(
    adopted, github, user_config, tmp_path, monkeypatch
):
    work, setup, _said = adopted
    monkeypatch.setattr(repo_kit, "__version__", "1.3.0")

    checks = run(work, github, setup, user_config, tmp_path)

    (pin,) = find(checks, ".repo-kit.toml valid")
    assert pin.status is Status.WARN  # doctor stays non-fatal
    assert "older than the pin" in pin.detail
    assert "uv tool install --force" in pin.detail


def test_invalid_config_fails_and_skips_what_needs_it(adopted, github, user_config, tmp_path):
    work, setup, _said = adopted
    config = work / ".repo-kit.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\nunknown = 1\n", encoding="utf-8")

    checks = run(work, github, setup, user_config, tmp_path)

    (bad,) = find(checks, ".repo-kit.toml invalid")
    assert bad.status is Status.FAIL
    assert "unknown" in bad.detail
    (skipped,) = find(checks, "managed files, plugin, base branch, App enrolment")
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


def test_an_old_stored_key_warns_and_a_missing_date_only_skips(
    adopted, github, user_config, tmp_path
):
    work, setup, _said = adopted

    undated = find(run(work, github, setup, user_config, tmp_path), "App key age")
    assert [(c.status, c.id) for c in undated] == [(Status.SKIP, "key-age")]

    path, sops = stored_credentials(tmp_path)
    sops.files[path]["release_bot"]["stored_at"] = "2000-01-01"
    checks = run(work, github, setup, user_config, tmp_path, sops=sops)

    (age,) = find(checks, "App key age")
    assert age.status is Status.WARN
    assert "gth-release-bot's key was stored" in age.detail
    assert not doctor.failed(checks)


def test_a_repo_still_on_the_rotated_out_key_is_named_as_such(
    adopted, github, user_config, tmp_path
):
    work, setup, _said = adopted
    path, sops = stored_credentials(tmp_path)
    new_pem = tmp_path / "new.pem"
    new_pem.write_text(NEW_PEM, encoding="utf-8")
    rotate_key(
        UserConfig(secrets_file=str(path)), tmp_path / "config.toml", sops, "release-bot",
        pem=new_pem, changed=[],
    )  # fmt: skip

    checks = run(work, github, setup, user_config, tmp_path, sops=sops)

    (bot,) = find(checks, "gth-release-bot: older key")
    assert "still holds the key `rk secrets rotate` replaced" in bot.detail
    assert "run `rk configure --replace-key`" in bot.detail
