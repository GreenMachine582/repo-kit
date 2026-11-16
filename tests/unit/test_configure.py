"""`rk configure`: enrolling a repo for both Apps, against an in-memory repo."""

from __future__ import annotations

from fakes import PEM, RELEASE_PEM, FakeRepoSetup

from repo_kit.bootstrap import configure, label_specs
from repo_kit.config import RepoConfig, UpdatePrsConfig
from repo_kit.credentials import PR_BOT, RELEASE_BOT, AppCredentials, Credentials


def creds(pr_client: str = "Iv23abc") -> Credentials:
    return {
        PR_BOT.key: AppCredentials(pr_client, PEM),
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
        RELEASE_BOT.client_id_variable: "Iv23rel",
    }
    assert setup.secrets == {
        PR_BOT.private_key_secret: PEM,
        RELEASE_BOT.private_key_secret: RELEASE_PEM,
    }
    assert len(changed) == 7
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


def test_replace_key_overwrites_both_secrets():
    setup = FakeRepoSetup()
    setup.secrets[PR_BOT.private_key_secret] = "old"
    setup.secrets[RELEASE_BOT.private_key_secret] = "old"
    changed, _ = enrol(setup, creds(), replace_key=True)
    assert setup.secrets[PR_BOT.private_key_secret] == PEM
    assert setup.secrets[RELEASE_BOT.private_key_secret] == RELEASE_PEM
    assert f"replaced secret {PR_BOT.private_key_secret}" in changed
    assert f"replaced secret {RELEASE_BOT.private_key_secret}" in changed


def test_a_dry_run_writes_nothing():
    setup = FakeRepoSetup()
    changed, said = enrol(setup, creds(), dry_run=True)
    assert changed == []
    assert setup.writes == []
    assert f"would set secret {PR_BOT.private_key_secret}" in said
    assert f"would set secret {RELEASE_BOT.private_key_secret}" in said
    assert "would create label needs-merge" in said


def test_label_specs_follow_the_config():
    config = UpdatePrsConfig(skip_labels=("wip", "hold"), conflict_label="clash")
    assert [name for name, _c, _d in label_specs(config)] == ["clash", "wip", "hold"]
