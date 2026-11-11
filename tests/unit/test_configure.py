"""`rk configure`: enrolling a repo for gth-pr-bot, against an in-memory repo."""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes import FakeRepoSetup

from repo_kit.bootstrap import (
    CLIENT_ID_VARIABLE,
    PRIVATE_KEY_SECRET,
    configure,
    label_specs,
)
from repo_kit.config import (
    ConfigError,
    PrBotConfig,
    RepoConfig,
    UpdatePrsConfig,
    UserConfig,
    parse_user_config,
)
from repo_kit.errors import RkError

KEY = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n"
SOURCE = Path("config.toml")


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "gth-pr-bot.pem"
    path.write_text(KEY, encoding="utf-8")
    return path


def user(key_file: Path, client_id: str = "Iv23abc") -> UserConfig:
    return UserConfig(pr_bot=PrBotConfig(client_id=client_id, private_key=str(key_file)))


def enrol(setup, user_config, **kwargs):
    changed: list[str] = []
    said: list[str] = []
    configure(
        setup,
        RepoConfig(),
        user_config,
        source=SOURCE,
        changed=changed,
        say=said.append,
        **kwargs,
    )
    return changed, said


def test_first_run_sets_everything(key_file):
    setup = FakeRepoSetup()
    changed, said = enrol(setup, user(key_file))
    assert set(setup.label_set) == {"needs-merge", "wip", "no-auto-update"}
    assert setup.vars == {CLIENT_ID_VARIABLE: "Iv23abc"}
    assert setup.secrets == {PRIVATE_KEY_SECRET: KEY}
    assert len(changed) == 5
    assert any("installations" in line for line in said)


def test_a_rerun_changes_nothing(key_file):
    setup = FakeRepoSetup()
    enrol(setup, user(key_file))
    setup.writes.clear()
    changed, said = enrol(setup, user(key_file))
    assert changed == []
    assert setup.writes == []
    assert any("already set" in line for line in said)


def test_existing_labels_are_left_alone(key_file):
    setup = FakeRepoSetup()
    setup.label_set["wip"] = ("000000", "the repo's own")
    enrol(setup, user(key_file))
    assert setup.label_set["wip"] == ("000000", "the repo's own")


def test_a_different_client_id_is_updated(key_file):
    setup = FakeRepoSetup()
    enrol(setup, user(key_file))
    setup.writes.clear()
    changed, _ = enrol(setup, user(key_file, client_id="Iv23new"))
    assert setup.vars[CLIENT_ID_VARIABLE] == "Iv23new"
    assert changed == [f"set variable {CLIENT_ID_VARIABLE}"]


def test_replace_key_overwrites_the_secret(key_file):
    setup = FakeRepoSetup()
    setup.secrets[PRIVATE_KEY_SECRET] = "old"
    changed, _ = enrol(setup, user(key_file), replace_key=True)
    assert setup.secrets[PRIVATE_KEY_SECRET] == KEY
    assert f"replaced secret {PRIVATE_KEY_SECRET}" in changed


def test_a_dry_run_writes_nothing(key_file):
    setup = FakeRepoSetup()
    changed, said = enrol(setup, user(key_file), dry_run=True)
    assert changed == []
    assert setup.writes == []
    assert f"would set secret {PRIVATE_KEY_SECRET}" in said
    assert "would create label needs-merge" in said


def test_missing_credentials_are_refused_with_the_toml_to_add():
    with pytest.raises(RkError, match=r"\[pr_bot\]"):
        enrol(FakeRepoSetup(), UserConfig())


def test_a_missing_key_file_is_refused_before_any_write(tmp_path):
    setup = FakeRepoSetup()
    with pytest.raises(RkError, match="doesn't exist"):
        enrol(setup, user(tmp_path / "nope.pem"))
    assert setup.writes == []


def test_a_non_pem_key_is_refused(tmp_path):
    path = tmp_path / "key.txt"
    path.write_text("not a key", encoding="utf-8")
    with pytest.raises(RkError, match="PEM"):
        enrol(FakeRepoSetup(), user(path))


def test_a_key_in_a_synced_folder_is_refused(tmp_path, monkeypatch):
    synced = tmp_path / "Sync"
    monkeypatch.setenv("OneDrive", str(synced))
    data = {"pr_bot": {"client_id": "x", "private_key": (synced / "k.pem").as_posix()}}
    with pytest.raises(ConfigError, match="synced folder"):
        parse_user_config(data, SOURCE)


def test_pr_bot_unknown_keys_are_errors():
    with pytest.raises(ConfigError, match="`pr_bot.key`"):
        parse_user_config({"pr_bot": {"key": "x"}}, SOURCE)


def test_label_specs_follow_the_config():
    config = UpdatePrsConfig(skip_labels=("wip", "hold"), conflict_label="clash")
    assert [name for name, _c, _d in label_specs(config)] == ["clash", "wip", "hold"]
