"""The encrypted credentials file and `rk secrets …`, with sops faked or (when installed) real."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from fakes import PEM, RELEASE_PEM, FakeSops, stored_credentials

from repo_kit import credentials as creds_module
from repo_kit.config import ConfigError, UserConfig, parse_user_config
from repo_kit.credentials import (
    AGE_KEY_ENV,
    PR_BOT,
    RELEASE_BOT,
    SopsCli,
    add_recipient,
    age_key_path,
    init,
    load_credentials,
    parse_sops_version,
    public_key,
    set_app,
    show,
)
from repo_kit.errors import RkError

SOURCE = Path("config.toml")
RECIPIENT = "age1" + "q" * 58


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """`~`, OneDrive and the age key all inside tmp_path: no test may reach the real home."""
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("OneDrive", str(home / "OneDrive"))
    monkeypatch.setenv(AGE_KEY_ENV, str(home / "age" / "keys.txt"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


def key_file(tmp_path: Path, recipient: str = RECIPIENT) -> Path:
    path = tmp_path / "age" / "keys.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# created: 2026-11-16T00:00:00Z\n# public key: {recipient}\nAGE-SECRET-KEY-1XYZ\n",
        encoding="utf-8",
    )
    return path


def pem_file(tmp_path: Path, text: str = PEM) -> Path:
    path = tmp_path / "downloaded.pem"
    path.write_text(text, encoding="utf-8")
    return path


# --- user config ---------------------------------------------------------------------------


def test_secrets_file_is_read_and_may_be_synced(tmp_path, monkeypatch):
    synced = tmp_path / "OneDrive"
    monkeypatch.setenv("OneDrive", str(synced))
    path = (synced / "repo-kit" / "secrets.sops.json").as_posix()
    config = parse_user_config({"secrets_file": path}, SOURCE)
    assert config.secrets_path == Path(path)


def test_an_old_pr_bot_table_names_the_migration():
    with pytest.raises(ConfigError, match="rk secrets set-app pr-bot"):
        parse_user_config({"pr_bot": {"client_id": "x", "private_key": "k.pem"}}, SOURCE)


# --- age -----------------------------------------------------------------------------------


def test_age_key_path_honours_the_env(monkeypatch, tmp_path):
    monkeypatch.setenv(AGE_KEY_ENV, str(tmp_path / "k.txt"))
    assert age_key_path() == tmp_path / "k.txt"


def test_age_key_path_defaults_like_sops(monkeypatch, tmp_path):
    monkeypatch.delenv(AGE_KEY_ENV, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert age_key_path() == tmp_path / "sops" / "age" / "keys.txt"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    if sys.platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        assert age_key_path() == tmp_path / "Roaming" / "sops" / "age" / "keys.txt"


def test_public_key_is_read_from_the_comment(tmp_path):
    assert public_key(key_file(tmp_path)) == RECIPIENT


def test_a_file_without_a_public_key_is_refused(tmp_path):
    path = tmp_path / "keys.txt"
    path.write_text("AGE-SECRET-KEY-1XYZ\n", encoding="utf-8")
    with pytest.raises(RkError, match="public key"):
        public_key(path)


def test_sops_version_is_parsed():
    assert parse_sops_version("sops 3.10.2 (latest)\n") == (3, 10)


# --- loading -------------------------------------------------------------------------------


def test_both_apps_load(tmp_path):
    path, sops = stored_credentials(tmp_path)
    loaded = load_credentials(UserConfig(secrets_file=str(path)), SOURCE, sops)
    assert loaded[PR_BOT.key].client_id == "Iv23abc"
    assert loaded[RELEASE_BOT.key].private_key == RELEASE_PEM
    assert "BEGIN" not in repr(loaded)


def test_no_secrets_file_points_at_init():
    with pytest.raises(RkError, match="rk secrets init"):
        load_credentials(UserConfig(), SOURCE, FakeSops())


def test_a_missing_secrets_file_points_at_init(tmp_path):
    config = UserConfig(secrets_file=str(tmp_path / "nope.json"))
    with pytest.raises(RkError, match="doesn't exist"):
        load_credentials(config, SOURCE, FakeSops())


def test_a_missing_app_names_the_set_app_command(tmp_path):
    path, sops = stored_credentials(tmp_path)
    del sops.files[path]["release_bot"]
    with pytest.raises(RkError, match="rk secrets set-app release-bot"):
        load_credentials(UserConfig(secrets_file=str(path)), SOURCE, sops)


def test_a_bad_key_is_refused(tmp_path):
    path, sops = stored_credentials(tmp_path)
    sops.files[path]["pr_bot"]["private_key"] = "not a key"
    with pytest.raises(RkError, match="isn't a PEM"):
        load_credentials(UserConfig(secrets_file=str(path)), SOURCE, sops)


# --- rk secrets init -----------------------------------------------------------------------


def test_init_does_the_whole_first_machine_setup_in_one_run(tmp_path, fake_home):
    source = fake_home / ".config" / "repo-kit" / "config.toml"
    made: list[Path] = []

    def keygen(path: Path) -> None:
        made.append(path)
        key_file(tmp_path)

    sops = FakeSops()
    changed: list[str] = []
    init(
        UserConfig(),
        source,
        sops,
        changed=changed,
        key_file=tmp_path / "age" / "keys.txt",
        keygen=keygen,
        say=lambda _m: None,
    )
    shared = fake_home / "OneDrive" / "repo-kit" / "secrets.sops.json"
    assert made == [tmp_path / "age" / "keys.txt"]
    assert source.read_text(encoding="utf-8") == (
        'secrets_file = "~/OneDrive/repo-kit/secrets.sops.json"\n'
    )
    assert sops.recipients[shared] == [RECIPIENT]
    assert changed == [
        f"created age key {tmp_path / 'age' / 'keys.txt'}",
        f"created {source} with secrets_file",
        f"created {shared}, readable by this machine's age key",
    ]


def test_init_adds_the_line_above_an_existing_config(tmp_path):
    source = tmp_path / "config.toml"
    source.write_text('worktree_root = "~/dev/wt"\n', encoding="utf-8")
    changed: list[str] = []
    init(
        UserConfig(),
        source,
        FakeSops(),
        changed=changed,
        file=str(tmp_path / "shared" / "s.json"),
        key_file=key_file(tmp_path),
        say=lambda _m: None,
    )
    assert parse_user_config(
        tomllib.loads(source.read_text(encoding="utf-8")), source
    ).secrets_path == (tmp_path / "shared" / "s.json")
    assert (tmp_path / "config.toml.bak").read_text(encoding="utf-8") == (
        'worktree_root = "~/dev/wt"\n'
    )
    assert changed[0] == f"added secrets_file to {source} (backup: config.toml.bak)"


def test_init_wont_silently_move_an_existing_secrets_file(tmp_path):
    path, sops = stored_credentials(tmp_path)
    with pytest.raises(RkError, match="already names"):
        init(
            UserConfig(secrets_file=str(path)),
            SOURCE,
            sops,
            changed=[],
            file=str(tmp_path / "other.json"),
            key_file=key_file(tmp_path),
        )


def test_init_creates_the_file_for_this_machine(tmp_path):
    sops = FakeSops()
    path = tmp_path / "sync" / "secrets.sops.json"
    changed: list[str] = []
    said: list[str] = []
    init(
        UserConfig(secrets_file=str(path)),
        SOURCE,
        sops,
        changed=changed,
        key_file=key_file(tmp_path),
        say=said.append,
    )
    assert sops.recipients[path] == [RECIPIENT]
    assert sops.files[path] == {"pr_bot": {}, "release_bot": {}}
    assert changed == [f"created {path}, readable by this machine's age key"]
    assert any("set-app release-bot" in line for line in said)


def test_init_on_a_second_machine_prints_the_add_recipient_command(tmp_path):
    path, sops = stored_credentials(tmp_path)
    sops.readable = False
    changed: list[str] = []
    said: list[str] = []
    init(
        UserConfig(secrets_file=str(path)),
        SOURCE,
        sops,
        changed=changed,
        key_file=key_file(tmp_path),
        say=said.append,
    )
    assert changed == []
    assert sops.writes == []
    assert any(f"rk secrets add-recipient {RECIPIENT}" in line for line in said)


def test_init_refuses_an_age_key_in_a_synced_folder(tmp_path, monkeypatch):
    synced = tmp_path / "OneDrive"
    monkeypatch.setenv("OneDrive", str(synced))
    with pytest.raises(RkError, match="must stay on this machine"):
        init(UserConfig(), SOURCE, FakeSops(), changed=[], key_file=synced / "keys.txt")


# --- rk secrets set-app / add-recipient / show ----------------------------------------------


def test_set_app_stores_both_values(tmp_path):
    path, sops = stored_credentials(tmp_path)
    changed: list[str] = []
    new_pem = PEM.replace("abc", "new")
    set_app(
        UserConfig(secrets_file=str(path)),
        SOURCE,
        sops,
        "release-bot",
        client_id="Iv23new",
        pem=pem_file(tmp_path, new_pem),
        changed=changed,
        say=lambda _m: None,
    )
    assert sops.files[path]["release_bot"] == {"client_id": "Iv23new", "private_key": new_pem}
    assert sops.files[path]["pr_bot"]["private_key"] == PEM
    assert changed == [f"stored gth-release-bot's Client ID and private key in {path}"]


def test_set_app_refuses_a_non_pem_before_writing(tmp_path):
    path, sops = stored_credentials(tmp_path)
    with pytest.raises(RkError, match="isn't a PEM"):
        set_app(
            UserConfig(secrets_file=str(path)),
            SOURCE,
            sops,
            "pr-bot",
            client_id="Iv23x",
            pem=pem_file(tmp_path, "nope"),
            changed=[],
        )
    assert sops.writes == []


def test_add_recipient_checks_the_key_format(tmp_path):
    path, sops = stored_credentials(tmp_path)
    config = UserConfig(secrets_file=str(path))
    with pytest.raises(RkError, match="isn't an age public key"):
        add_recipient(config, SOURCE, sops, "ssh-ed25519 AAAA", changed=[])
    changed: list[str] = []
    add_recipient(config, SOURCE, sops, RECIPIENT, changed=changed)
    assert sops.recipients[path] == [RECIPIENT]
    assert changed == [f"added recipient {RECIPIENT} to {path}"]


def test_show_lists_client_ids_and_never_a_key(tmp_path):
    path, sops = stored_credentials(tmp_path)
    del sops.files[path]["release_bot"]["private_key"]
    said: list[str] = []
    code = show(UserConfig(secrets_file=str(path)), SOURCE, sops, say=said.append)
    assert code == 1
    assert "  gth-pr-bot: Client ID Iv23abc, private key present" in said
    assert "  gth-release-bot: Client ID Iv23rel, private key missing" in said
    assert not any("BEGIN" in line for line in said)


# --- SopsCli: what reaches the sops binary --------------------------------------------------


class FakeRun:
    """Stands in for `subprocess.run` inside credentials.py."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, stdout: str = "") -> None:
        self.calls: list[list[str]] = []
        self.stdin: list[object] = []
        self.stdout = stdout
        monkeypatch.setattr(creds_module.subprocess, "run", self)

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        self.stdin.append(kwargs.get("input"))
        out = "sops 3.10.2 (latest)\n" if argv[1:] == ["--version"] else self.stdout
        return subprocess.CompletedProcess(argv, 0, out, "")


def test_set_value_sends_the_key_on_stdin_only(monkeypatch, tmp_path):
    run = FakeRun(monkeypatch)
    SopsCli().set_value(tmp_path / "s.json", ("pr_bot", "private_key"), PEM)
    set_call = run.calls[-1]
    assert set_call[:2] == ["sops", "set"]
    assert "--value-stdin" in set_call
    assert set_call[-1] == '["pr_bot"]["private_key"]'
    assert run.stdin[-1] == json.dumps(PEM)
    assert not any("BEGIN" in arg for call in run.calls for arg in call)


def test_decrypt_reads_json(monkeypatch, tmp_path):
    FakeRun(monkeypatch, stdout='{"pr_bot": {"client_id": "Iv23abc"}}')
    assert SopsCli().decrypt(tmp_path / "s.json") == {"pr_bot": {"client_id": "Iv23abc"}}


def test_an_old_sops_is_refused_before_writing(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def old(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "sops 3.8.1\n", "")

    monkeypatch.setattr(creds_module.subprocess, "run", old)
    with pytest.raises(RkError, match="3.10 or newer"):
        SopsCli().set_value(tmp_path / "s.json", ("pr_bot", "client_id"), "x")
    assert calls == [["sops", "--version"]]


# --- the real binaries, when installed ------------------------------------------------------


@pytest.mark.skipif(
    not (shutil.which("sops") and shutil.which("age-keygen")), reason="needs sops and age"
)
def test_round_trip_with_real_sops(tmp_path, monkeypatch):
    key = tmp_path / "keys.txt"
    monkeypatch.setenv(AGE_KEY_ENV, str(key))
    path = tmp_path / "secrets.sops.json"
    config = UserConfig(secrets_file=str(path))
    sops = SopsCli()

    init(config, SOURCE, sops, changed=[], say=lambda _m: None)
    for app, pem in (("pr-bot", PEM), ("release-bot", RELEASE_PEM)):
        set_app(
            config,
            SOURCE,
            sops,
            app,
            client_id=f"Iv23{app[0]}",
            pem=pem_file(tmp_path, pem),
            changed=[],
            say=lambda _m: None,
        )

    assert "PRIVATE KEY" not in path.read_text(encoding="utf-8")  # only age ciphertext
    loaded = load_credentials(config, SOURCE, sops)
    assert loaded[PR_BOT.key].private_key == PEM
    assert loaded[RELEASE_BOT.key].client_id == "Iv23r"


def test_the_suggested_secrets_file_starts_from_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setenv("OneDrive", str(tmp_path / "OneDrive"))
    assert creds_module.suggested_secrets_file() == "~/OneDrive/repo-kit/secrets.sops.json"
    monkeypatch.setenv("OneDrive", str(tmp_path.parent / "elsewhere"))
    assert creds_module.suggested_secrets_file().endswith("elsewhere/repo-kit/secrets.sops.json")
