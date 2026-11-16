"""The GitHub Apps' credentials, kept in one sops + age encrypted file, and `rk secrets …`.

The file is JSON, named by `secrets_file` in the user config. Encrypted, it can sit in a synced
folder; each machine holds only its own age key, which must stay local. sops and age-keygen are
binaries called with argument lists, like `gh`: nothing secret ever goes in an argument. See
docs/github-app.md.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from repo_kit.config import UserConfig, load_user_config, synced_folder, user_config_path
from repo_kit.errors import RkError

MIN_SOPS = (3, 10)  # `sops set --value-stdin`
AGE_KEY_ENV = "SOPS_AGE_KEY_FILE"
_AGE_RECIPIENT = re.compile(r"age1[0-9a-z]{58}")


@dataclass(frozen=True)
class App:
    """A GitHub App rk enrols repos for, and where its credentials go."""

    key: str  # its table in the secrets file
    arg: str  # its name on the command line
    name: str
    client_id_variable: str
    private_key_secret: str


PR_BOT = App("pr_bot", "pr-bot", "gth-pr-bot", "GTH_PR_BOT_CLIENT_ID", "GTH_PR_BOT_PRIVATE_KEY")
RELEASE_BOT = App(
    "release_bot",
    "release-bot",
    "gth-release-bot",
    "GTH_RELEASE_BOT_CLIENT_ID",
    "GTH_RELEASE_BOT_PRIVATE_KEY",
)
APPS = (PR_BOT, RELEASE_BOT)


@dataclass(frozen=True)
class AppCredentials:
    client_id: str
    private_key: str

    def __repr__(self) -> str:  # never show the key, even in a traceback
        return f"AppCredentials(client_id={self.client_id!r}, private_key=<hidden>)"


# App.key -> its credentials; every App in APPS is present once loaded.
Credentials = dict[str, AppCredentials]


class SopsError(RkError):
    """sops is missing, too old, or a call failed."""


# --- sops ----------------------------------------------------------------------------------


class Sops(Protocol):
    """The sops calls rk makes. `SopsCli` runs the binary; tests use an in-memory fake."""

    def version(self) -> tuple[int, int] | None:
        """The sops on PATH, or None if there isn't one."""
        ...

    def decrypt(self, path: Path) -> dict[str, Any]: ...

    def set_value(self, path: Path, keys: tuple[str, ...], value: str) -> None:
        """Set one string in place. The value goes to sops on stdin, never as an argument."""
        ...

    def create(self, path: Path, recipient: str) -> None:
        """Write a new encrypted file, with one age recipient and no credentials."""
        ...

    def add_recipient(self, path: Path, recipient: str) -> None: ...


def parse_sops_version(text: str) -> tuple[int, int]:
    """`sops 3.10.2 (latest)` -> (3, 10)."""
    match = re.search(r"sops (\d+)\.(\d+)", text)
    if not match:
        raise SopsError(f"can't read the sops version from {text!r}")
    return int(match[1]), int(match[2])


# The placeholder a new file is encrypted from: no secrets, so it may touch the disk.
EMPTY_FILE: dict[str, dict[str, str]] = {app.key: {} for app in APPS}


class SopsCli:
    """`Sops` backed by the `sops` binary."""

    def _sops(self, *args: str, stdin: str | None = None) -> str:
        try:
            proc = subprocess.run(
                ["sops", *args],
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise SopsError(
                "sops is not on PATH: install sops and age (docs/github-app.md)"
            ) from exc
        if proc.returncode != 0:
            raise SopsError(f"`sops {' '.join(args)}` failed:\n{proc.stderr.strip()}")
        return proc.stdout

    def version(self) -> tuple[int, int] | None:
        try:
            return parse_sops_version(self._sops("--version"))
        except SopsError:
            return None

    def _require_version(self) -> None:
        have = self.version()
        if have is None:
            raise SopsError("sops is not on PATH: install sops and age (docs/github-app.md)")
        if have < MIN_SOPS:
            raise SopsError(f"rk needs sops {'.'.join(map(str, MIN_SOPS))} or newer")

    def decrypt(self, path: Path) -> dict[str, Any]:
        out = self._sops("decrypt", "--input-type", "json", "--output-type", "json", str(path))
        data = json.loads(out)
        if not isinstance(data, dict):
            raise SopsError(f"{path} doesn't hold a JSON object")
        return data

    def set_value(self, path: Path, keys: tuple[str, ...], value: str) -> None:
        self._require_version()
        index = "".join(f"[{json.dumps(k)}]" for k in keys)
        self._sops(
            "set", "--input-type", "json", "--value-stdin", str(path), index,
            stdin=json.dumps(value),
        )  # fmt: skip

    def create(self, path: Path, recipient: str) -> None:
        self._require_version()
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "empty.json"
            plain.write_text(json.dumps(EMPTY_FILE), encoding="utf-8")
            self._sops(
                "encrypt", "--age", recipient, "--input-type", "json", "--output-type", "json",
                "--output", str(path), str(plain),
            )  # fmt: skip

    def add_recipient(self, path: Path, recipient: str) -> None:
        self._require_version()
        self._sops("rotate", "--input-type", "json", "-i", "--add-age", recipient, str(path))


# --- age -----------------------------------------------------------------------------------


def age_key_path() -> Path:
    """Where sops looks for this machine's age key: `SOPS_AGE_KEY_FILE`, else its default."""
    env = os.environ.get(AGE_KEY_ENV)
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        base = Path(xdg)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"
    return base / "sops" / "age" / "keys.txt"


def check_age_key_location(path: Path) -> None:
    synced = synced_folder(path)
    if synced:
        raise RkError(
            f"the age key {path} is inside a synced folder ({synced}). It decrypts every "
            f"credential, so it must stay on this machine: move it and set {AGE_KEY_ENV}."
        )


def public_key(key_file: Path) -> str:
    """The `# public key: age1…` line age-keygen writes above the key."""
    for line in key_file.read_text(encoding="utf-8").splitlines():
        match = re.match(r"#\s*public key:\s*(age1\S+)", line)
        if match:
            return match[1]
    raise RkError(f"{key_file} has no `# public key: age1…` line: is it an age key file?")


def run_age_keygen(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["age-keygen", "-o", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise RkError("age-keygen is not on PATH: install age (docs/github-app.md)") from exc
    if proc.returncode != 0:
        raise RkError(f"`age-keygen -o {path}` failed:\n{proc.stderr.strip()}")


# --- credentials ---------------------------------------------------------------------------


def pem_problem(text: str) -> str | None:
    if "-----BEGIN" not in text or "PRIVATE KEY-----" not in text:
        return "isn't a PEM private key (the App's downloaded .pem)"
    return None


def _set_app_hint(app: App) -> str:
    return f"rk secrets set-app {app.arg} --client-id <Client ID> --pem <downloaded .pem>"


def secrets_path(user_config: UserConfig, source: Path) -> Path:
    path = user_config.secrets_path
    if path is None:
        raise RkError(
            f"{source} doesn't name the credentials file: run `rk secrets init`, which prints "
            "the `secrets_file = …` line to add"
        )
    if not path.is_file():
        raise RkError(
            f"`secrets_file` in {source} points at {path}, which doesn't exist: "
            "run `rk secrets init`"
        )
    return path


def _app_table(data: dict[str, Any], app: App) -> dict[str, Any]:
    table = data.get(app.key)
    return table if isinstance(table, dict) else {}


def load_credentials(user_config: UserConfig, source: Path, sops: Sops) -> Credentials:
    """Every App's credentials, decrypted and checked before anything is written."""
    path = secrets_path(user_config, source)
    data = sops.decrypt(path)
    credentials: Credentials = {}
    problems: list[str] = []
    for app in APPS:
        table = _app_table(data, app)
        client_id = table.get("client_id")
        key = table.get("private_key")
        if not isinstance(client_id, str) or not client_id or not isinstance(key, str) or not key:
            problems.append(f"{app.name} isn't in {path}: `{_set_app_hint(app)}`")
            continue
        problem = pem_problem(key)
        if problem:
            problems.append(f"{app.name}'s private_key {problem}: `{_set_app_hint(app)}`")
            continue
        credentials[app.key] = AppCredentials(client_id, key)
    if problems:
        raise RkError("\n".join(problems))
    return credentials


# --- rk secrets ----------------------------------------------------------------------------


def app_by_arg(arg: str) -> App:
    for app in APPS:
        if app.arg == arg:
            return app
    raise RkError(f"unknown App {arg!r} (known: {', '.join(a.arg for a in APPS)})")


def suggested_secrets_file() -> str:
    """In OneDrive when there is one, written from `~` so the line works under any user name."""
    onedrive = os.environ.get("OneDrive")  # noqa: SIM112  # Windows sets it in this case
    path = (Path(onedrive) if onedrive else Path.home()) / "repo-kit" / "secrets.sops.json"
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return path.as_posix()


def init(
    user_config: UserConfig,
    source: Path,
    sops: Sops,
    *,
    changed: list[str],
    key_file: Path | None = None,
    keygen: Callable[[Path], None] = run_age_keygen,
    say: Callable[[str], None] = print,
) -> None:
    """Set this machine up: its age key, then the credentials file. Safe to re-run."""
    key_file = key_file or age_key_path()
    check_age_key_location(key_file)
    if not key_file.is_file():
        keygen(key_file)
        changed.append(f"created age key {key_file}")
    recipient = public_key(key_file)
    say(f"This machine's age public key: {recipient}")

    path = user_config.secrets_path
    if path is None:
        create = "" if source.is_file() else " (create it: it doesn't exist yet)"
        say(
            f"Next, tell rk where the shared credentials file goes. This machine's user config is "
            f"{source}{create}. It stays on this machine, so each machine has its own. Put this "
            "line at the top, before any [table]:\n\n"
            f'secrets_file = "{suggested_secrets_file()}"\n\n'
            "The file it names is encrypted, so a synced folder is fine, and `~` means each "
            "machine's own home folder. Then run `rk secrets init` again to create it."
        )
        return
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        sops.create(path, recipient)
        changed.append(f"created {path}, readable by this machine's age key")
        for app in APPS:
            say(f"Next, store {app.name}'s credentials: {_set_app_hint(app)}")
        return
    try:
        sops.decrypt(path)
    except RkError:
        say(
            f"{path} exists but this machine can't decrypt it. On a machine that can, run:\n\n"
            f"rk secrets add-recipient {recipient}\n\n"
            "then let the file sync here and run `rk secrets show`."
        )
        return
    say(f"{path} decrypts on this machine: `rk secrets show` lists what it holds.")


def set_app(
    user_config: UserConfig,
    source: Path,
    sops: Sops,
    app_arg: str,
    *,
    client_id: str,
    pem: Path,
    changed: list[str],
    say: Callable[[str], None] = print,
) -> None:
    """Store one App's Client ID and private key in the credentials file."""
    app = app_by_arg(app_arg)
    path = secrets_path(user_config, source)
    client_id = client_id.strip()
    if not client_id or any(c.isspace() for c in client_id):
        raise RkError(f"{client_id!r} isn't a Client ID (GitHub shows it as `Iv23…`)")
    if not pem.is_file():
        raise RkError(f"{pem} doesn't exist")
    key = pem.read_text(encoding="utf-8")
    problem = pem_problem(key)
    if problem:
        raise RkError(f"{pem} {problem}")

    sops.set_value(path, (app.key, "client_id"), client_id)
    sops.set_value(path, (app.key, "private_key"), key)
    stored = _app_table(sops.decrypt(path), app)
    if stored.get("client_id") != client_id or stored.get("private_key") != key:
        raise RkError(f"{path} doesn't read back what was stored for {app.name}: check it by hand")
    changed.append(f"stored {app.name}'s Client ID and private key in {path}")
    say(
        f"{pem} can be deleted now. Then run `rk configure` in each repo "
        "(`--replace-key` if this replaced a key)."
    )


def add_recipient(
    user_config: UserConfig,
    source: Path,
    sops: Sops,
    recipient: str,
    *,
    changed: list[str],
) -> None:
    """Let another machine's age key decrypt the credentials file."""
    if not _AGE_RECIPIENT.fullmatch(recipient):
        raise RkError(f"{recipient!r} isn't an age public key (age1 followed by 58 characters)")
    path = secrets_path(user_config, source)
    sops.add_recipient(path, recipient)
    changed.append(f"added recipient {recipient} to {path}")


def show(
    user_config: UserConfig, source: Path, sops: Sops, *, say: Callable[[str], None] = print
) -> int:
    """List each App's Client ID and whether its key parses. Never prints a key."""
    path = secrets_path(user_config, source)
    data = sops.decrypt(path)
    missing = False
    say(str(path))
    for app in APPS:
        table = _app_table(data, app)
        client_id = table.get("client_id") or "missing"
        key = table.get("private_key")
        if not isinstance(key, str) or not key:
            state, missing = "missing", True
        elif pem_problem(key):
            state, missing = "not a PEM private key", True
        else:
            state = "present"
        say(f"  {app.name}: Client ID {client_id}, private key {state}")
    return 1 if missing else 0


def _loaded() -> tuple[UserConfig, Path, SopsCli]:
    source = user_config_path()
    return load_user_config(source), source, SopsCli()


def run_init(*, changed: list[str]) -> None:
    init(*_loaded(), changed=changed)


def run_set_app(app_arg: str, *, client_id: str, pem: Path, changed: list[str]) -> None:
    set_app(*_loaded(), app_arg, client_id=client_id, pem=pem.expanduser(), changed=changed)


def run_add_recipient(recipient: str, *, changed: list[str]) -> None:
    add_recipient(*_loaded(), recipient, changed=changed)


def run_show() -> int:
    return show(*_loaded())
