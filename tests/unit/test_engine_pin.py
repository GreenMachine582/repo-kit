"""An rk older than the repo's pin can't know what that release wants, so it refuses."""

import pytest

import repo_kit
from repo_kit import files
from repo_kit.config import RepoConfig
from repo_kit.errors import RkError


def config(pin: str) -> RepoConfig:
    return RepoConfig(version=pin)


@pytest.mark.parametrize(
    ("engine", "pin", "behind"),
    [
        ("0.3.0", "0.4.0", True),
        ("0.4.0", "0.4.0", False),
        ("0.5.0", "0.4.0", False),
        ("0.10.0", "0.9.0", False),  # numbers, not strings
        ("0.0.0", "0.4.0", False),  # unreleased: can't be compared
        ("0.3.0", "latest", False),  # an odd pin is doctor's to report
    ],
)
def test_engine_behind_pin(engine, pin, behind):
    assert files.engine_behind_pin(engine, pin) is behind


def test_older_refuses_with_the_install_command(monkeypatch):
    monkeypatch.setattr(repo_kit, "__version__", "0.3.0")
    with pytest.raises(RkError) as exc:
        files.require_engine_not_behind(config("0.4.0"))
    text = str(exc.value)
    assert "pinned to 0.4.0 but this rk is 0.3.0" in text
    assert "uv tool install --force git+https://github.com/GreenMachine582/repo-kit@v0.4.0" in text


@pytest.mark.parametrize("engine", ["0.4.0", "0.5.0"])
def test_equal_or_newer_passes_quietly(monkeypatch, engine):
    monkeypatch.setattr(repo_kit, "__version__", engine)
    said: list[str] = []
    files.require_engine_not_behind(config("0.4.0"), said.append)
    assert said == []


def test_unreleased_warns_and_goes_on(monkeypatch):
    monkeypatch.setattr(repo_kit, "__version__", "0.0.0")
    said: list[str] = []
    files.require_engine_not_behind(config("0.4.0"), said.append)
    assert len(said) == 1
    assert "unreleased build" in said[0]


def test_require_pin_names_the_install_when_older(monkeypatch):
    monkeypatch.setattr(repo_kit, "__version__", "0.3.0")
    with pytest.raises(RkError, match=r"install rk 0\.4\.0"):
        files.require_pin(config("0.4.0"))
