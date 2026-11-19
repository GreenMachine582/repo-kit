"""An rk older than the pin refuses `rk github` and `rk sync`, before any GitHub call or write."""

from __future__ import annotations

import pytest

import repo_kit
from repo_kit import github_config
from repo_kit.errors import RkError
from repo_kit.files import run_sync


@pytest.fixture
def pinned(clone, monkeypatch):
    work = clone("work")
    (work / ".repo-kit.toml").write_text(
        'version = "0.4.0"\nprofile = "dev-main"\n', encoding="utf-8"
    )
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(repo_kit, "__version__", "0.3.0")
    return work


@pytest.mark.parametrize("apply_changes", [False, True])
def test_github_refuses_an_older_rk(pinned, apply_changes):
    with pytest.raises(RkError, match=r"pinned to 0\.4\.0 but this rk is 0\.3\.0"):
        github_config.run_github(pinned, apply_changes=apply_changes)


@pytest.mark.parametrize("check", [False, True])
def test_sync_refuses_an_older_rk(pinned, check):
    with pytest.raises(RkError, match=r"install rk 0\.4\.0"):
        run_sync(pinned, check=check, changed=[], say=lambda _: None)


def test_github_goes_on_with_a_newer_rk(pinned, monkeypatch):
    monkeypatch.setattr(repo_kit, "__version__", "0.5.0")
    # Past the pin check it reaches origin, a local path here, so no GitHub call is made.
    with pytest.raises(RkError, match="isn't on GitHub"):
        github_config.run_github(pinned, apply_changes=False)


def test_github_warns_and_goes_on_with_an_unreleased_rk(pinned, monkeypatch, capsys):
    monkeypatch.setattr(repo_kit, "__version__", "0.0.0")
    with pytest.raises(RkError, match="isn't on GitHub"):
        github_config.run_github(pinned, apply_changes=False)
    assert "unreleased build" in capsys.readouterr().out
