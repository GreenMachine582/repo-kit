"""The pieces of `rk install` that need no repo: the seed config and the plugin settings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import repo_kit
from repo_kit.bootstrap import (
    SETTINGS_PATH,
    detect_release_type,
    plan_plugin_settings,
    seed_config,
)
from repo_kit.config import parse_repo_config_text
from repo_kit.errors import RkError
from repo_kit.files import render, template_variables, templates_root


@pytest.mark.parametrize(
    ("present", "expected"),
    [
        (["pyproject.toml"], "python"),
        (["package.json"], "node"),
        (["pyproject.toml", "package.json"], "python"),
        ([], "simple"),
    ],
)
def test_the_release_type_follows_the_project_files(tmp_path, present, expected):
    for name in present:
        (tmp_path / name).write_text("", encoding="utf-8")
    assert detect_release_type(tmp_path) == expected


def test_the_seed_pins_the_running_release(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_kit, "__version__", "2.1.0")
    config = seed_config(tmp_path, "demo", "dev-main")
    assert (config.version, config.profile, config.project.name) == ("2.1.0", "dev-main", "demo")


def test_the_seeded_template_parses_back_to_the_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_kit, "__version__", "2.1.0")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    config = seed_config(tmp_path, "demo", "dev-main")
    template = templates_root() / "common" / "repo-kit.toml"
    text = render(
        template.read_text(encoding="utf-8"), template_variables(config, "o", "demo"), "seed"
    )
    assert parse_repo_config_text(text, Path(".repo-kit.toml")) == config


def test_settings_are_created_when_missing(tmp_path):
    text = plan_plugin_settings(tmp_path)
    assert text is not None
    assert text.endswith("}\n")
    assert list(json.loads(text)) == ["extraKnownMarketplaces", "enabledPlugins"]


def test_settings_that_already_have_both_are_left_alone(tmp_path):
    first = plan_plugin_settings(tmp_path)
    assert first is not None
    (tmp_path / ".claude").mkdir()
    (tmp_path / SETTINGS_PATH).write_text(first, encoding="utf-8")
    assert plan_plugin_settings(tmp_path) is None


def test_a_marketplace_source_the_repo_chose_is_kept(tmp_path):
    (tmp_path / ".claude").mkdir()
    own = {"extraKnownMarketplaces": {"repo-kit": {"source": {"source": "directory", "path": "x"}}}}
    (tmp_path / SETTINGS_PATH).write_text(json.dumps(own), encoding="utf-8")
    text = plan_plugin_settings(tmp_path)
    assert text is not None
    after = json.loads(text)
    assert after["extraKnownMarketplaces"] == own["extraKnownMarketplaces"]
    assert after["enabledPlugins"] == {"repo-kit@repo-kit": True}


def test_settings_that_are_a_directory_are_refused(tmp_path):
    (tmp_path / SETTINGS_PATH).mkdir(parents=True)
    with pytest.raises(RkError, match="is a directory"):
        plan_plugin_settings(tmp_path)
