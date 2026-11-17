from pathlib import Path

import pytest

from repo_kit.config import (
    ConfigError,
    ExtraFile,
    RepoConfig,
    UserConfig,
    load_repo_config,
    load_user_config,
    parse_repo_config,
    parse_user_config,
)

SRC = Path(".repo-kit.toml")

DOCUMENTED_EXAMPLE = """
version = "1.4.0"
profile = "dev-main"

[project]
name = "greentechhub-core"
release_type = "python"

[checks]
required = ["test"]
pr_title = "not-conventional"
commit_header_max = 100

[release]
app = "gth-release-bot"
back_merge = true

[update_prs]
enabled = true
skip_labels = ["no-auto-update", "wip"]
conflict_label = "needs-merge"

[worktree]
setup = ["uv venv", "uv pip install -e .[dev]"]
check = "./scripts/check.sh"
env_dirs = [".venv"]

[extras]
use_local_gth = true
gth_packages = ["greentechhub-core", "greentechhub-fastapi"]

[overrides]
files.".github/workflows/release.yml" = "unmanaged"
"""


def test_missing_repo_config_is_all_defaults(tmp_path):
    config = load_repo_config(tmp_path)
    assert config == RepoConfig()
    assert config.default_base == "dev"
    assert config.update_prs.conflict_label == "needs-merge"
    assert config.worktree.env_dirs == (".venv",)


def test_documented_example_parses(tmp_path):
    (tmp_path / ".repo-kit.toml").write_text(DOCUMENTED_EXAMPLE, encoding="utf-8")
    config = load_repo_config(tmp_path)
    assert config.version == "1.4.0"
    assert config.project.name == "greentechhub-core"
    assert config.checks.required == ("test",)
    assert config.worktree.setup == ("uv venv", "uv pip install -e .[dev]")
    assert config.extras.gth_packages == ("greentechhub-core", "greentechhub-fastapi")
    assert config.overrides.files == {".github/workflows/release.yml": "unmanaged"}


@pytest.mark.parametrize(
    ("data", "key"),
    [
        ({"verison": "1.0.0"}, "`verison`"),
        ({"worktree": {"setpu": []}}, "`worktree.setpu`"),
        ({"update_prs": {"enabled": True, "labels": []}}, "`update_prs.labels`"),
        ({"overrides": {"paths": {}}}, "`overrides.paths`"),
    ],
)
def test_unknown_keys_are_errors(data, key):
    with pytest.raises(ConfigError, match="unknown key") as exc:
        parse_repo_config(data, SRC)
    assert key in str(exc.value)


@pytest.mark.parametrize(
    "data",
    [
        {"version": 1},
        {"checks": {"commit_header_max": "100"}},
        {"checks": {"commit_header_max": True}},
        {"update_prs": {"enabled": 1}},
        {"worktree": {"setup": "uv venv"}},
        {"worktree": {"env_dirs": [1]}},
        {"project": "x"},
    ],
)
def test_wrong_types_are_errors(data):
    with pytest.raises(ConfigError, match="must be"):
        parse_repo_config(data, SRC)


def test_unknown_profile_is_an_error():
    with pytest.raises(ConfigError, match="unknown profile 'main-only'"):
        parse_repo_config({"profile": "main-only"}, SRC)


def test_unknown_pr_title_rule_is_an_error():
    with pytest.raises(ConfigError, match="`checks.pr_title` must be one of"):
        parse_repo_config({"checks": {"pr_title": "squash"}}, SRC)


@pytest.mark.parametrize(
    "entry",
    [".", "..", "../x", "a/../b", "./.venv", "*", ".v*", "[ab]", "?", "/abs", "C:\\venv", "\\x"]
    + ["", " ", "a//b", ".git", ":(top)src"],
)
def test_env_dirs_must_be_plain_relative_names(entry):
    with pytest.raises(ConfigError, match="`worktree.env_dirs` entry"):
        parse_repo_config({"worktree": {"env_dirs": [".venv", entry]}}, SRC)


@pytest.mark.parametrize("entry", [".venv", "venv", "node_modules", "build/cache", "my env"])
def test_env_dirs_accepts_plain_names(entry):
    config = parse_repo_config({"worktree": {"env_dirs": [entry]}}, SRC)
    assert config.worktree.env_dirs == (entry,)


def test_extra_files_parse_into_entries():
    config = parse_repo_config(
        {
            "project": {
                "extra_files": [
                    {"path": "src/x/version.py"},
                    {"path": ".repo-kit.toml", "type": "toml", "jsonpath": "$.version"},
                ]
            }
        },
        SRC,
    )
    assert config.project.extra_files == (
        ExtraFile("src/x/version.py"),
        ExtraFile(".repo-kit.toml", "toml", "$.version"),
    )
    assert RepoConfig().project.extra_files == ()


def test_unknown_extra_file_keys_are_errors():
    with pytest.raises(ConfigError, match="unknown key") as exc:
        parse_repo_config({"project": {"extra_files": [{"path": "a", "jsonpth": "$"}]}}, SRC)
    assert "`project.extra_files[0].jsonpth`" in str(exc.value)


@pytest.mark.parametrize(
    "entry",
    [{"path": ""}, {"path": "../a"}, {"path": "/a"}, {"path": "C:\\a"}, {"path": "a/*.py"}]
    + [{"path": "a//b"}, {"path": "./a"}],
)
def test_extra_file_paths_must_be_plain_relative_files(entry):
    with pytest.raises(ConfigError, match="plain relative file path"):
        parse_repo_config({"project": {"extra_files": [entry]}}, SRC)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"path": "a", "type": "xml"}, "must be one of"),
        ({"path": "a", "type": "toml"}, "needs a `jsonpath`"),
        ({"path": "a", "type": "json", "jsonpath": "version"}, "needs a `jsonpath`"),
        ({"path": "a", "jsonpath": "$.v"}, "only applies to json, toml and yaml"),
    ],
)
def test_extra_file_types_and_jsonpaths_are_checked(entry, message):
    with pytest.raises(ConfigError, match=message):
        parse_repo_config({"project": {"extra_files": [entry]}}, SRC)


def test_extra_files_must_be_a_list_of_tables():
    with pytest.raises(ConfigError, match="must be a list"):
        parse_repo_config({"project": {"extra_files": "a.py"}}, SRC)
    with pytest.raises(ConfigError, match="must be a table"):
        parse_repo_config({"project": {"extra_files": ["a.py"]}}, SRC)


def test_override_mode_must_be_unmanaged():
    with pytest.raises(ConfigError, match='must be "unmanaged"'):
        parse_repo_config({"overrides": {"files": {"a.yml": "managed"}}}, SRC)


def test_bad_toml_names_the_file(tmp_path):
    (tmp_path / ".repo-kit.toml").write_text("version = ", encoding="utf-8")
    with pytest.raises(ConfigError, match="repo-kit.toml"):
        load_repo_config(tmp_path)


def test_user_config_defaults_and_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REPO_KIT_CONFIG", str(tmp_path / "missing.toml"))
    assert load_user_config() == UserConfig()

    path = tmp_path / "config.toml"
    path.write_text(f'worktree_root = "{(tmp_path / "wt").as_posix()}"\n', encoding="utf-8")
    monkeypatch.setenv("REPO_KIT_CONFIG", str(path))
    assert load_user_config().worktree_root == tmp_path / "wt"


def test_user_config_unknown_key():
    with pytest.raises(ConfigError, match="`worktree_dir`"):
        parse_user_config({"worktree_dir": "x"}, SRC)


@pytest.mark.parametrize("folder", ["OneDrive", "OneDrive - Work", "Dropbox"])
def test_worktree_root_in_synced_folder_is_rejected(tmp_path, monkeypatch, folder):
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(var, raising=False)
    root = (tmp_path / folder / "wt").as_posix()
    with pytest.raises(ConfigError, match="synced folder"):
        parse_user_config({"worktree_root": root}, SRC)


def test_worktree_root_under_onedrive_env_is_rejected(tmp_path, monkeypatch):
    synced = tmp_path / "Sync"
    monkeypatch.setenv("OneDrive", str(synced))
    with pytest.raises(ConfigError, match="synced folder"):
        parse_user_config({"worktree_root": (synced / "wt").as_posix()}, SRC)


def test_key_max_age_days_defaults_to_a_year_and_must_be_positive():
    assert parse_user_config({}, SRC).key_max_age_days == 365
    assert parse_user_config({"key_max_age_days": 90}, SRC).key_max_age_days == 90
    with pytest.raises(ConfigError, match="key_max_age_days"):
        parse_user_config({"key_max_age_days": 0}, SRC)
