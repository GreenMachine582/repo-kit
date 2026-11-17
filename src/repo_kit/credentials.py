"""The GitHub Apps' credentials, kept in one sops + age encrypted file, and `rk secrets …`.

The file is JSON, named by `secrets_file` in the user config. Encrypted, it can sit in a synced
folder; each machine holds only its own age key, which must stay local. sops and age-keygen are
binaries called with argument lists, like `gh`: nothing secret ever goes in an argument. See
docs/github-app.md.
"""

from __future__ import annotations

import base64
import hashlib
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

from repo_kit.config import (
    RepoConfig,
    UserConfig,
    load_user_config,
    synced_folder,
    user_config_path,
)
from repo_kit.errors import RkError

MIN_SOPS = (3, 10)  # `sops set --value-stdin`

# Where a missing binary is explained: what to install, and the new terminal winget needs.
_INSTALL_HINT = (
    "install sops and age, then open a new terminal (docs/github-app.md#installing-sops-and-age)"
)
AGE_KEY_ENV = "SOPS_AGE_KEY_FILE"
PREVIOUS_FINGERPRINT_KEY = "previous_key_fingerprint"
LABELS_KEY = "_recipients"  # recipient -> label, inside the encrypted data
_AGE_RECIPIENT = re.compile(r"age1[0-9a-z]{58}")


@dataclass(frozen=True)
class App:
    """A GitHub App rk enrols repos for, and where its credentials go."""

    key: str  # its table in the secrets file
    arg: str  # its name on the command line
    name: str
    client_id_variable: str
    private_key_secret: str
    # The secret's key fingerprint (`key_fingerprint`): a secret can't be read back, so this is
    # how `rk doctor` and `rk configure` tell which key it holds.
    key_fingerprint_variable: str
    # The GitHub environment holding the private key. Only one branch may use it, so a workflow
    # on any other pushed branch can't read the key (docs/github-app.md#where-the-keys-live).
    environment: str


PR_BOT = App(
    "pr_bot",
    "pr-bot",
    "gth-pr-bot",
    "GTH_PR_BOT_CLIENT_ID",
    "GTH_PR_BOT_PRIVATE_KEY",
    "GTH_PR_BOT_KEY_SHA256",
    "gth-pr-bot",
)
RELEASE_BOT = App(
    "release_bot",
    "release-bot",
    "gth-release-bot",
    "GTH_RELEASE_BOT_CLIENT_ID",
    "GTH_RELEASE_BOT_PRIVATE_KEY",
    "GTH_RELEASE_BOT_KEY_SHA256",
    "gth-release-bot",
)
APPS = (PR_BOT, RELEASE_BOT)


def environment_branch(app: App, config: RepoConfig) -> str:
    """The one branch allowed to use the App's environment. update-prs runs as `workflow_run`,
    which is always on the default branch; releases run on pushes to the release branch."""
    return config.default_base if app is PR_BOT else config.release_branch


def apps_for(config: RepoConfig) -> tuple[App, ...]:
    """The Apps a repo uses: gth-pr-bot only with update-prs on; gth-release-bot always, since
    every `dev-main` repo cuts releases."""
    return tuple(app for app in APPS if app is not PR_BOT or config.update_prs.enabled)


@dataclass(frozen=True)
class AppCredentials:
    client_id: str
    private_key: str
    # The fingerprint of the key `rk secrets rotate` replaced, so doctor can tell a repo still on
    # the previous key from one holding something else.
    previous_fingerprint: str | None = None

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

    def remove_recipient(self, path: Path, recipient: str) -> None:
        """Drop one age recipient and re-encrypt, so its key can't decrypt the file any more."""
        ...

    def unset_value(self, path: Path, keys: tuple[str, ...]) -> None: ...

    def list_recipients(self, path: Path) -> list[str]:
        """The age recipients the file is encrypted to. Needs no key: sops keeps them in clear."""
        ...


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
            raise SopsError(f"sops is not on PATH: {_INSTALL_HINT}") from exc
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
            raise SopsError(f"sops is not on PATH: {_INSTALL_HINT}")
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

    def remove_recipient(self, path: Path, recipient: str) -> None:
        self._require_version()
        self._sops("rotate", "--input-type", "json", "-i", "--rm-age", recipient, str(path))

    def unset_value(self, path: Path, keys: tuple[str, ...]) -> None:
        self._require_version()
        index = "".join(f"[{json.dumps(k)}]" for k in keys)
        self._sops("unset", "--input-type", "json", str(path), index)

    def list_recipients(self, path: Path) -> list[str]:
        try:
            meta = json.loads(path.read_text(encoding="utf-8")).get("sops", {})
            entries = meta.get("age") or []
        except (OSError, ValueError, AttributeError) as exc:
            raise SopsError(f"can't read {path}'s sops metadata: {exc}") from None
        found = [e.get("recipient") for e in entries if isinstance(e, dict)]
        return [r for r in found if isinstance(r, str)]


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
        raise RkError(f"age-keygen is not on PATH: {_INSTALL_HINT}") from exc
    if proc.returncode != 0:
        raise RkError(f"`age-keygen -o {path}` failed:\n{proc.stderr.strip()}")


# --- credentials ---------------------------------------------------------------------------


_PEM_LINE = re.compile(r"-----(BEGIN|END) ([A-Z0-9 ]+)-----")
_PEM_PRIVATE_LABELS = ("RSA PRIVATE KEY", "PRIVATE KEY")  # PKCS#1 and PKCS#8
_RSA_OID = bytes.fromhex("2a864886f70d010101")  # rsaEncryption, inside a PKCS#8 key


def _pem_parts(text: str) -> tuple[str, bytes]:
    """The label and DER bytes of the one PEM block in `text`. Raises ValueError with a message
    that never includes key material."""
    text = text.lstrip(chr(0xFEFF))
    labels = [m[2] for m in _PEM_LINE.finditer(text) if m[1] == "BEGIN"]
    if not labels:
        raise ValueError("isn't a PEM private key (no -----BEGIN ...----- line)")
    if len(labels) > 1:
        raise ValueError("holds more than one PEM block: it should hold only the private key")
    label = labels[0]
    if label.endswith("PUBLIC KEY") or label == "CERTIFICATE":
        raise ValueError(
            f"is a {label.lower()}, not the private key: generate a private key on the App's "
            "settings page and use the .pem it downloads"
        )
    if label == "ENCRYPTED PRIVATE KEY":
        raise ValueError("is a passphrase-protected key: GitHub's downloaded .pem isn't")
    if label not in _PEM_PRIVATE_LABELS:
        raise ValueError(f"is a {label.lower()}: a GitHub App needs an RSA private key")
    body: list[str] = []
    inside = ended = False
    for raw in text.splitlines():
        line = raw.strip()
        found = _PEM_LINE.fullmatch(line)
        if found and found[1] == "BEGIN":
            inside = True
        elif found and found[1] == "END":
            if found[2] != label:
                raise ValueError("has an END line that doesn't match its BEGIN line")
            ended = True
            break
        elif inside and line:
            body.append(line)
    if not ended:
        raise ValueError("isn't a complete PEM key (no matching -----END ...----- line)")
    if not body:
        raise ValueError("has an empty PEM body")
    try:
        der = base64.b64decode("".join(body), validate=True)
    except ValueError:  # binascii.Error is one
        raise ValueError("isn't a PEM private key (its body isn't valid base64)") from None
    if not der or der[0] != 0x30:  # every PKCS#1 and PKCS#8 key is an ASN.1 SEQUENCE
        raise ValueError("isn't a PEM private key (its body isn't a DER key)")
    if label == "PRIVATE KEY" and _RSA_OID not in der[:64]:
        raise ValueError("is a PKCS#8 key but not an RSA one: a GitHub App needs an RSA key")
    return label, der


def key_fingerprint(pem: str) -> str:
    """SHA-256 (hex) of a PEM key's DER bytes. Safe to store and show: it can't rebuild the key."""
    return hashlib.sha256(_pem_parts(pem)[1]).hexdigest()


def pem_problem(text: str) -> str | None:
    """Why `text` isn't a usable App private key, or None. Never quotes the key."""
    try:
        _pem_parts(text)
    except ValueError as exc:
        return str(exc)
    return None


def normalise_pem(text: str) -> str:
    """The key with LF line endings, no BOM or stray blank lines, 64-character body lines and
    one trailing newline. Raises ValueError if it isn't a usable key."""
    label, der = _pem_parts(text)
    body = base64.b64encode(der).decode("ascii")
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    return "\n".join([f"-----BEGIN {label}-----", *lines, f"-----END {label}-----"]) + "\n"


def conflict_copies(path: Path) -> list[Path]:
    """Copies a sync client made of `path` in a conflict, such as OneDrive's
    `secrets.sops-DESKTOP-AB12.json` beside `secrets.sops.json`. One directory listing, no
    writes; an unreadable directory has none."""
    stem, suffix = path.stem, path.suffix
    try:
        return sorted(
            sibling
            for sibling in path.parent.iterdir()
            if sibling.name != path.name
            and sibling.name.startswith(f"{stem}-")
            and sibling.name.endswith(suffix)
            and sibling.is_file()
        )
    except OSError:
        return []


def conflict_copy_warning(path: Path) -> str | None:
    copies = conflict_copies(path)
    if not copies:
        return None
    names = ", ".join(copy.name for copy in copies)
    return (
        f"{path.parent} also holds {names}, a sync conflict copy of {path.name}: merge the two "
        "(`rk secrets set-app` for any App the copy changed), then delete the copy"
    )


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


def load_credentials(
    user_config: UserConfig, source: Path, sops: Sops, apps: tuple[App, ...] = APPS
) -> Credentials:
    """The credentials of `apps`, decrypted and checked before anything is written. An App the
    repo doesn't use may be missing from the file."""
    path = secrets_path(user_config, source)
    data = sops.decrypt(path)
    credentials: Credentials = {}
    problems: list[str] = []
    for app in apps:
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
        previous = table.get(PREVIOUS_FINGERPRINT_KEY)
        credentials[app.key] = AppCredentials(
            client_id, key, previous if isinstance(previous, str) and previous else None
        )
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


def record_secrets_file(source: Path, value: str, *, changed: list[str]) -> None:
    """Add `secrets_file = …` to the top of this machine's user config, creating it if needed.

    Top-level keys must come before any [table], so the line goes first; the rest is kept as is,
    and the old file is backed up next to it.
    """
    line = f"secrets_file = {json.dumps(value)}\n"  # a JSON string is a valid TOML basic string
    if source.is_file():
        old = source.read_text(encoding="utf-8")
        backup = source.with_name(source.name + ".bak")
        backup.write_text(old, encoding="utf-8")
        source.write_text(line + old, encoding="utf-8")
        changed.append(f"added secrets_file to {source} (backup: {backup.name})")
    else:
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(line, encoding="utf-8")
        changed.append(f"created {source} with secrets_file")


def init(
    user_config: UserConfig,
    source: Path,
    sops: Sops,
    *,
    changed: list[str],
    file: str | None = None,
    key_file: Path | None = None,
    keygen: Callable[[Path], None] = run_age_keygen,
    say: Callable[[str], None] = print,
) -> int:
    """Set this machine up in one go: its age key, where the shared credentials file is (in this
    machine's user config), and the file itself if no machine has made it yet. Safe to re-run.

    Returns 1 while this machine can't decrypt the file yet (another machine must add it as a
    recipient), else 0: a script can tell whether setup is finished.
    """
    key_file = key_file or age_key_path()
    check_age_key_location(key_file)
    if not key_file.is_file():
        keygen(key_file)
        changed.append(f"created age key {key_file}")
    recipient = public_key(key_file)
    say(f"This machine's age public key: {recipient}")

    path = user_config.secrets_path
    if path is None:
        value = file or suggested_secrets_file()
        if not value.startswith("~"):
            value = Path(value).as_posix()
        record_secrets_file(source, value, changed=changed)
        path = Path(value).expanduser()
    elif file and Path(file).expanduser() != path:
        raise RkError(
            f"{source} already names {user_config.secrets_file}: "
            "edit `secrets_file` there to move it"
        )
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        sops.create(path, recipient)
        changed.append(f"created {path}, readable by this machine's age key")
        say(
            "Another machine joins later with `rk secrets init` too: run it there only once this "
            "file has synced, or it makes a second file."
        )
        for app in APPS:
            say(f"Next, store {app.name}'s credentials: {_set_app_hint(app)}")
        return 0
    try:
        sops.decrypt(path)
    except RkError:
        say(
            f"{path} exists but this machine can't decrypt it. On a machine that can, run:\n\n"
            f"rk secrets add-recipient {recipient}\n\n"
            "then let the file sync here and run `rk secrets show`."
        )
        return 1
    say(f"{path} decrypts on this machine: `rk secrets show` lists what it holds.")
    return 0


def read_pem(pem: Path) -> str:
    """The normalised key in a downloaded `.pem`, or an RkError saying what's wrong with the
    file. Nothing here prints or quotes the key."""
    if pem.is_dir():
        raise RkError(f"{pem} is a folder: give the path of the .pem file")
    if not pem.is_file():
        raise RkError(f"{pem} doesn't exist")
    try:
        raw = pem.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        raise RkError(f"{pem} isn't a PEM private key (it isn't a text file)") from None
    try:
        return normalise_pem(raw)
    except ValueError as exc:
        raise RkError(f"{pem} {exc}") from None


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
    key = read_pem(pem)

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


@dataclass(frozen=True)
class Rotation:
    """What `rotate_key` did: enough for a caller (`rk secrets rotate`, later `rk fleet`) to go
    on and replace the key in each repo."""

    app: App
    new_fingerprint: str
    previous_fingerprint: str | None  # None if the file held no readable key before


def rotate_key(
    user_config: UserConfig,
    source: Path,
    sops: Sops,
    app_arg: str,
    *,
    pem: Path,
    changed: list[str],
) -> Rotation:
    """Store a new private key for an App that's already in the credentials file, and keep the
    old key's fingerprint beside it. The Client ID stays. Writes nothing if the `.pem` is
    unusable or is the key already stored. This is the part `rk fleet` calls; it prints nothing
    and touches no repo."""
    app = app_by_arg(app_arg)
    path = secrets_path(user_config, source)
    new_key = read_pem(pem)
    table = _app_table(sops.decrypt(path), app)
    old_key = table.get("private_key")
    if not table.get("client_id") or not isinstance(old_key, str) or not old_key:
        raise RkError(f"{app.name} isn't in {path}: `{_set_app_hint(app)}` stores its first key")
    new_fingerprint = key_fingerprint(new_key)
    previous = key_fingerprint(old_key) if pem_problem(old_key) is None else None
    if previous == new_fingerprint:
        raise RkError(
            f"{pem} is the key already stored for {app.name}: generate a new private key in the "
            "App's settings first"
        )
    if previous:
        sops.set_value(path, (app.key, PREVIOUS_FINGERPRINT_KEY), previous)
    sops.set_value(path, (app.key, "private_key"), new_key)
    stored = _app_table(sops.decrypt(path), app)
    if stored.get("private_key") != new_key:
        raise RkError(f"{path} doesn't read back the new key for {app.name}: check it by hand")
    changed.append(f"stored a new private key for {app.name} in {path}")
    return Rotation(app, new_fingerprint, previous)


@dataclass(frozen=True)
class KnownRepo:
    checkout: Path  # the main checkout, where `rk configure` runs
    name: str  # owner/repo
    apps: tuple[App, ...]


def known_repos(user_config: UserConfig, cwd: Path) -> list[KnownRepo]:
    """Repos rk can find without a registry: the one in `cwd` and any with a worktree under
    `worktree_root`. Each is read from its own `.repo-kit.toml`; one that can't be read is left
    out. `rk fleet` replaces this with its own list of adopting repos."""
    from repo_kit import git
    from repo_kit.config import load_repo_config

    candidates = [cwd]
    root = user_config.worktree_root
    if root.is_dir():
        for group in sorted(p for p in root.iterdir() if p.is_dir()):
            inside = sorted(p for p in group.iterdir() if (p / ".git").exists())
            candidates.extend(inside[:1])  # one worktree is enough to find the repo
    found: dict[Path, KnownRepo] = {}
    for candidate in candidates:
        try:
            main = git.main_checkout(candidate)
            owner, repo = git.parse_owner_repo(git.remote_url(main))
            config = load_repo_config(main)
        except (RkError, OSError):
            continue
        found.setdefault(main, KnownRepo(main, f"{owner}/{repo}", apps_for(config)))
    return sorted(found.values(), key=lambda r: r.name)


def configure_commands(app: App, repos: list[KnownRepo]) -> list[str]:
    """The commands that put a rotated key into each repo that uses `app`."""
    return [
        f'cd "{repo.checkout}" && rk configure --replace-key' for repo in repos if app in repo.apps
    ]


def rotate(
    user_config: UserConfig,
    source: Path,
    sops: Sops,
    app_arg: str,
    *,
    pem: Path,
    repos: list[KnownRepo],
    changed: list[str],
    say: Callable[[str], None] = print,
) -> Rotation:
    """`rk secrets rotate`: store the new key, then say how to replace it in each repo."""
    rotation = rotate_key(user_config, source, sops, app_arg, pem=pem, changed=changed)
    app = rotation.app
    commands = configure_commands(app, repos)
    say(f"{pem} can be deleted now.")
    if commands:
        say(f"Put the new key in each repo that uses {app.name}:")
        for command in commands:
            say(f"  {command}")
    else:
        say("rk found no repos to name: run `rk configure --replace-key` in each enrolled repo.")
    say(
        "Then run `rk doctor` in each (it flags a repo still on the previous key) and delete the "
        f"old key in {app.name}'s settings."
    )
    return rotation


def add_recipient(
    user_config: UserConfig,
    source: Path,
    sops: Sops,
    recipient: str,
    *,
    label: str | None = None,
    changed: list[str],
) -> None:
    """Let another machine's age key decrypt the credentials file."""
    if not _AGE_RECIPIENT.fullmatch(recipient):
        raise RkError(f"{recipient!r} isn't an age public key (age1 followed by 58 characters)")
    path = secrets_path(user_config, source)
    label = label.strip() if label else None
    if label and label.startswith("age1"):
        raise RkError("a label can't start with `age1`: it would look like a recipient")
    sops.add_recipient(path, recipient)
    changed.append(f"added recipient {recipient} to {path}")
    if label:
        # Labels live inside the encrypted data: the recipients list in sops' metadata has no
        # room for them, and a sidecar file could drift from the file it describes.
        sops.set_value(path, (LABELS_KEY, recipient), label)
        changed.append(f"labelled {recipient} as {label!r}")


def _labels(sops: Sops, path: Path) -> dict[str, str] | None:
    """Recipient -> label, or None if this machine can't decrypt the file."""
    try:
        table = sops.decrypt(path).get(LABELS_KEY)
    except SopsError:
        return None
    if not isinstance(table, dict):
        return {}
    return {k: v for k, v in table.items() if isinstance(v, str)}


def _own_recipient() -> str | None:
    try:
        return public_key(age_key_path())
    except (OSError, RkError):
        return None


def recipients(
    user_config: UserConfig,
    source: Path,
    sops: Sops,
    *,
    own: str | None = None,
    say: Callable[[str], None] = print,
) -> None:
    """List the age recipients the credentials file is encrypted to, with their labels."""
    path = secrets_path(user_config, source)
    found = sops.list_recipients(path)
    labels = _labels(sops, path)
    say(str(path))
    if not found:
        say("  no recipients")
    for recipient in found:
        notes = []
        if labels is not None and labels.get(recipient):
            notes.append(labels[recipient])
        if recipient == own:
            notes.append("this machine")
        say(f"  {recipient}" + (f"  ({', '.join(notes)})" if notes else ""))
    if labels is None:
        say("  This machine can't decrypt the file, so labels aren't shown.")


def remove_recipient(
    user_config: UserConfig,
    source: Path,
    sops: Sops,
    target: str,
    *,
    own: str | None = None,
    include_self: bool = False,
    changed: list[str],
) -> None:
    """Stop an age key decrypting the credentials file, by recipient or label, and re-encrypt.
    Refuses to drop the last recipient, or this machine's own unless `include_self`."""
    path = secrets_path(user_config, source)
    found = sops.list_recipients(path)
    labels = _labels(sops, path) or {}
    if target in found:
        recipient = target
    else:
        matches = [r for r in found if labels.get(r) == target]
        if not matches:
            raise RkError(
                f"{target!r} isn't a recipient or label of {path}: see `rk secrets recipients`"
            )
        if len(matches) > 1:
            raise RkError(
                f"label {target!r} matches {len(matches)} recipients: use the age1... key"
            )
        recipient = matches[0]
    if len(found) == 1:
        raise RkError("that's the only recipient: removing it would lock everyone out")
    if recipient == own and not include_self:
        raise RkError(
            f"{recipient} is this machine's own key: removing it stops this machine decrypting "
            "the file. Pass --include-self if you mean that."
        )
    if recipient in labels:  # while this machine can still decrypt
        sops.unset_value(path, (LABELS_KEY, recipient))
    sops.remove_recipient(path, recipient)
    changed.append(f"removed recipient {recipient} from {path}")


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
    warning = conflict_copy_warning(path)
    if warning:
        say(f"Warning: {warning}")
    return 1 if missing else 0


def _loaded() -> tuple[UserConfig, Path, SopsCli]:
    source = user_config_path()
    return load_user_config(source), source, SopsCli()


def run_init(*, file: str | None, changed: list[str]) -> int:
    return init(*_loaded(), file=file, changed=changed)


def run_set_app(app_arg: str, *, client_id: str, pem: Path, changed: list[str]) -> None:
    set_app(*_loaded(), app_arg, client_id=client_id, pem=pem.expanduser(), changed=changed)


def run_rotate(app_arg: str, *, pem: Path, changed: list[str]) -> None:
    user_config, source, sops = _loaded()
    rotate(
        user_config, source, sops, app_arg, pem=pem.expanduser(),
        repos=known_repos(user_config, Path.cwd()), changed=changed,
    )  # fmt: skip


def run_add_recipient(recipient: str, *, label: str | None = None, changed: list[str]) -> None:
    add_recipient(*_loaded(), recipient, label=label, changed=changed)


def run_recipients() -> None:
    recipients(*_loaded(), own=_own_recipient())


def run_remove_recipient(target: str, *, include_self: bool, changed: list[str]) -> None:
    remove_recipient(
        *_loaded(), target, own=_own_recipient(), include_self=include_self, changed=changed
    )


def run_show() -> int:
    return show(*_loaded())
