"""`rk configure`: enrolling a repo for both Apps, against an in-memory repo."""

from __future__ import annotations

from fakes import NEW_PEM, PEM, RELEASE_PEM, FakeRepoSetup

from repo_kit.bootstrap import configure, label_specs
from repo_kit.config import RepoConfig, UpdatePrsConfig
from repo_kit.credentials import (
    PR_BOT,
    RELEASE_BOT,
    AppCredentials,
    Credentials,
    environment_branch,
    key_fingerprint,
)


def creds(pr_client: str = "Iv23abc", pr_key: str = PEM) -> Credentials:
    return {
        PR_BOT.key: AppCredentials(pr_client, pr_key),
        RELEASE_BOT.key: AppCredentials("Iv23rel", RELEASE_PEM),
    }


def enrol(setup, credentials, **kwargs):
    changed: list[str] = []
    said: list[str] = []
    configure(setup, RepoConfig(), credentials, changed=changed, say=said.append, **kwargs)
    return changed, said


def test_first_run_sets_everything_for_both_apps():
    setup = FakeRepoSetup()
    changed, said = enrol(setup, creds())
    assert set(setup.label_set) == {"needs-merge", "wip", "no-auto-update"}
    assert setup.vars == {
        PR_BOT.client_id_variable: "Iv23abc",
        PR_BOT.key_fingerprint_variable: key_fingerprint(PEM),
        RELEASE_BOT.client_id_variable: "Iv23rel",
        RELEASE_BOT.key_fingerprint_variable: key_fingerprint(RELEASE_PEM),
    }
    assert setup.environments == {PR_BOT.environment: ["dev"], RELEASE_BOT.environment: ["main"]}
    assert setup.key(PR_BOT) == PEM
    assert setup.key(RELEASE_BOT) == RELEASE_PEM
    assert setup.secrets == {}  # nothing repo-level, which any pushed branch could read
    assert len(changed) == 11
    assert any("installations" in line and "gth-release-bot" in line for line in said)
    assert not any("PRIVATE KEY-----" in line for line in [*changed, *said])


def test_a_rerun_changes_nothing():
    setup = FakeRepoSetup()
    enrol(setup, creds())
    setup.writes.clear()
    changed, said = enrol(setup, creds())
    assert changed == []
    assert setup.writes == []
    assert sum("already set" in line for line in said) == 2


def test_existing_labels_are_left_alone():
    setup = FakeRepoSetup()
    setup.label_set["wip"] = ("000000", "the repo's own")
    enrol(setup, creds())
    assert setup.label_set["wip"] == ("000000", "the repo's own")


def test_a_different_client_id_is_updated():
    setup = FakeRepoSetup()
    enrol(setup, creds())
    setup.writes.clear()
    changed, _ = enrol(setup, creds(pr_client="Iv23new"))
    assert setup.vars[PR_BOT.client_id_variable] == "Iv23new"
    assert changed == [f"set variable {PR_BOT.client_id_variable}"]


def in_env(app) -> str:
    return f"secret {app.private_key_secret} in environment {app.environment}"


def test_replace_key_overwrites_both_secrets():
    setup = FakeRepoSetup()
    enrol(setup, creds())
    setup.env_secrets[PR_BOT.environment][PR_BOT.private_key_secret] = "old"
    setup.env_secrets[RELEASE_BOT.environment][RELEASE_BOT.private_key_secret] = "old"
    changed, _ = enrol(setup, creds(), replace_key=True)
    assert setup.key(PR_BOT) == PEM
    assert setup.key(RELEASE_BOT) == RELEASE_PEM
    assert f"replaced {in_env(PR_BOT)}" in changed
    assert f"replaced {in_env(RELEASE_BOT)}" in changed
    assert setup.vars[PR_BOT.key_fingerprint_variable] == key_fingerprint(PEM)
    assert setup.vars[RELEASE_BOT.key_fingerprint_variable] == key_fingerprint(RELEASE_PEM)


def test_a_rotated_key_is_named_but_not_written_without_replace_key():
    setup = FakeRepoSetup()
    enrol(setup, creds())
    setup.writes.clear()
    changed, said = enrol(setup, creds(pr_key=NEW_PEM))
    assert changed == []
    assert setup.writes == []
    assert setup.key(PR_BOT) == PEM
    assert (
        f"{in_env(PR_BOT)} holds a different key from your credentials file: "
        "`rk configure --replace-key` replaces it"
    ) in said
    assert f"{in_env(RELEASE_BOT)} is already set" in " ".join(said)


def test_replace_key_after_a_rotation_updates_the_fingerprint():
    setup = FakeRepoSetup()
    enrol(setup, creds())
    changed, _ = enrol(setup, creds(pr_key=NEW_PEM), replace_key=True)
    assert setup.key(PR_BOT) == NEW_PEM
    assert setup.vars[PR_BOT.key_fingerprint_variable] == key_fingerprint(NEW_PEM)
    assert f"set variable {PR_BOT.key_fingerprint_variable}" in changed
    # the release bot's key didn't change, so neither does its fingerprint
    assert f"set variable {RELEASE_BOT.key_fingerprint_variable}" not in changed


def test_a_secret_set_before_fingerprints_existed_is_named():
    setup = FakeRepoSetup()
    setup.environments[PR_BOT.environment] = ["dev"]
    setup.env_secrets[PR_BOT.environment] = {PR_BOT.private_key_secret: "set by an older rk"}
    changed, said = enrol(setup, creds())
    assert setup.key(PR_BOT) == "set by an older rk"
    assert PR_BOT.key_fingerprint_variable not in setup.vars
    assert f"set variable {PR_BOT.key_fingerprint_variable}" not in changed
    (line,) = [line for line in said if PR_BOT.private_key_secret in line]
    assert f"variable {PR_BOT.key_fingerprint_variable} is missing" in line
    assert "rk configure --replace-key" in line


def test_a_dry_run_writes_nothing():
    setup = FakeRepoSetup()
    changed, said = enrol(setup, creds(), dry_run=True)
    assert changed == []
    assert setup.writes == []
    assert f"would create environment {PR_BOT.environment}, usable only from branch dev" in said
    assert f"would set {in_env(PR_BOT)}" in said
    assert f"would set {in_env(RELEASE_BOT)}" in said
    assert f"would set variable {PR_BOT.key_fingerprint_variable}" in said
    assert f"would set variable {RELEASE_BOT.key_fingerprint_variable}" in said
    assert "would create label needs-merge" in said


def test_label_specs_follow_the_config():
    config = UpdatePrsConfig(skip_labels=("wip", "hold"), conflict_label="clash")
    assert [name for name, _c, _d in label_specs(config)] == ["clash", "wip", "hold"]


def test_a_repo_without_update_prs_needs_only_the_release_bot():
    setup = FakeRepoSetup()
    config = RepoConfig(update_prs=UpdatePrsConfig(enabled=False))
    only_release = {RELEASE_BOT.key: AppCredentials("Iv23rel", RELEASE_PEM)}
    changed: list[str] = []
    said: list[str] = []
    configure(setup, config, only_release, changed=changed, say=said.append)
    assert setup.vars == {
        RELEASE_BOT.client_id_variable: "Iv23rel",
        RELEASE_BOT.key_fingerprint_variable: key_fingerprint(RELEASE_PEM),
    }
    assert setup.key(RELEASE_BOT) == RELEASE_PEM
    assert PR_BOT.environment not in setup.environments
    assert not any(PR_BOT.name in line for line in said)


def test_repo_level_keys_move_into_their_environments():
    # An rk before environments stored both keys where any pushed branch could read them.
    setup = FakeRepoSetup()
    setup.secrets[PR_BOT.private_key_secret] = "old repo-level"
    setup.secrets[RELEASE_BOT.private_key_secret] = "old repo-level"
    setup.vars[PR_BOT.key_fingerprint_variable] = key_fingerprint(PEM)
    changed, _ = enrol(setup, creds())
    assert setup.secrets == {}
    assert setup.key(PR_BOT) == PEM  # written from the credentials file: a secret can't be copied
    assert setup.key(RELEASE_BOT) == RELEASE_PEM
    assert setup.writes.index(f"secret {PR_BOT.private_key_secret} in {PR_BOT.environment}") < (
        setup.writes.index(f"delete secret {PR_BOT.private_key_secret}")
    )
    assert any(
        line.startswith(f"deleted repo-level secret {PR_BOT.private_key_secret}")
        for line in changed
    )

    setup.writes.clear()
    assert enrol(setup, creds())[0] == []
    assert setup.writes == []


def test_a_dry_run_names_the_migration_and_writes_nothing():
    setup = FakeRepoSetup()
    setup.secrets[RELEASE_BOT.private_key_secret] = "old repo-level"
    changed, said = enrol(setup, creds(), dry_run=True)
    assert changed == []
    assert setup.writes == []
    assert (
        f"would delete repo-level secret {RELEASE_BOT.private_key_secret}, which any pushed branch "
        "can read"
    ) in said


def test_an_environment_open_to_other_branches_is_restricted():
    setup = FakeRepoSetup()
    enrol(setup, creds())
    setup.environments[PR_BOT.environment] = ["*"]
    setup.writes.clear()
    changed, _ = enrol(setup, creds())
    assert setup.environments[PR_BOT.environment] == ["dev"]
    assert changed == [f"restricted environment {PR_BOT.environment}, usable only from branch dev"]
    assert setup.key(PR_BOT) == PEM  # the key it already held stays


def test_the_environment_branches_follow_the_config():
    config = RepoConfig()
    assert environment_branch(PR_BOT, config) == config.default_base == "dev"
    assert environment_branch(RELEASE_BOT, config) == config.release_branch == "main"
